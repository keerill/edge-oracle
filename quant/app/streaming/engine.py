"""Live arb stream engine: WS book deltas -> arb re-eval -> Redis pub/sub.

``run_stream`` is the timing-free, injectable test seam: it consumes an async iterator of raw
CLOB market-channel frames, keeps the ``BookStore`` live, re-runs the **existing** set-arb math
(``app.math.arb.evaluate_market``) on every affected market, enriches via the **existing**
``advise`` (so the live edge equals the periodic scan / backtest — live == replay), dedups by
net edge, and publishes high-net-edge signals through an injected ``publish`` callback.

Stream-only: nothing here writes the ``signals`` table. Per-frame isolation — one bad frame is
logged and skipped, never killing the loop.

``connect_clob_ws`` (the real websockets source) and the CLI are thin wrappers around the seam.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store as store_mod
from app.math.arb import ArbParams, evaluate_market
from app.models.advisor import AdvisedSignal
from app.observability.alert_bus import publish_alert
from app.observability.alerts import evaluate_ws_drop
from app.observability.logging import configure_logging
from app.observability.metrics import (
    SIGNALS,
    WS_CONNECTS,
    WS_DROPS,
    WS_UP,
    start_metrics_server,
)
from app.observability.sentry import init_sentry
from app.streaming.book_state import BookStore
from app.streaming.redis_bus import publish_signal
from app.advisor.view import advise

logger = logging.getLogger(__name__)

Publish = Callable[[AdvisedSignal], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def arb_params(settings: Settings) -> ArbParams:
    """Map the EDGE_-prefixed money knobs onto the pure ArbParams (keeps math pure)."""
    return ArbParams(
        set_size=settings.arb_set_size,
        gas=settings.arb_gas,
        slippage=settings.arb_slippage,
        min_net_edge=settings.arb_min_net_edge,
    )


def _signal_id(market_id: str) -> str:
    """Stable id per market so the SSE layer / client replace the row in place (not append)."""
    return f"set_arb:{market_id}"


async def _on_market(
    store: BookStore,
    market_id: str,
    params: ArbParams,
    *,
    publish: Publish,
    bankroll: Decimal,
    now: Callable[[], datetime],
    dedup: dict[str, Decimal],
) -> None:
    """Re-evaluate one market's arb and publish if it's a new/changed high-net-edge signal."""
    books = store.market_books(market_id)
    if books is None:  # both legs not seen yet
        return
    market, yes_book, no_book = books
    signal = evaluate_market(
        yes_book,
        no_book,
        params,
        market_id=market.market_id,
        condition_id=market.condition_id,
        at=now(),
    )
    if signal is None:
        return
    # Dedup: only publish when the net edge actually moved (deltas re-fire constantly).
    if dedup.get(market_id) == signal.net_edge:
        return
    dedup[market_id] = signal.net_edge
    advised = advise(
        signal,
        signal_id=_signal_id(market_id),
        market_question=market.question,
        bankroll=bankroll,
    )
    await publish(advised)
    SIGNALS.labels(advised.strategy, "stream").inc()


async def run_stream(
    messages: AsyncIterator[dict],
    store: BookStore,
    params: ArbParams,
    *,
    publish: Publish,
    bankroll: Decimal,
    now: Callable[[], datetime] = _utcnow,
    dedup: dict[str, Decimal] | None = None,
) -> None:
    """Consume WS frames, keep books live, publish new high-net-edge arb signals.

    The test seam: feed a mock async iterator of raw frame dicts and a capturing ``publish``.
    """
    from app.polymarket.schemas import parse_ws_message  # local: untrusted-boundary parse

    if dedup is None:
        dedup = {}
    async for raw in messages:
        try:
            parsed = parse_ws_message(raw)
            if parsed is None:  # tick_size_change / last_trade_price / unknown -> ignore
                continue
            market_id = store.apply(parsed)
            if market_id is None:  # frame for a token we don't track
                continue
            await _on_market(
                store,
                market_id,
                params,
                publish=publish,
                bankroll=bankroll,
                now=now,
                dedup=dedup,
            )
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the stream
            logger.warning("skipping bad WS frame: %r", exc)


# --- Real CLOB WebSocket source ----------------------------------------------


async def connect_clob_ws(
    url: str,
    token_ids: list[str],
    *,
    reconnect_delay_s: float = 2.0,
    on_drop: Callable[[Exception], Awaitable[None]] | None = None,
    connect: Callable[[str], Any] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_reconnects: int | None = None,
) -> AsyncIterator[dict]:
    """Connect to the CLOB market channel, subscribe to ``token_ids``, yield raw frames.

    Reconnects with a fixed delay on any drop; ``on_drop(exc)`` is awaited on each drop (the
    live path publishes a ws_drop alert there). ``connect``/``sleep``/``max_reconnects`` are
    injectable seams for tests (default ``connect`` is ``websockets.connect``). The market
    channel sends an array of ``book`` snapshots on subscribe and single delta frames after —
    both are flattened to dicts.
    """
    connector = connect
    if connector is None:
        import websockets  # local import: only needed for the live path

        connector = websockets.connect

    subscribe = json.dumps({"assets_ids": token_ids, "type": "market"})
    reconnects = 0
    while True:
        try:
            async with connector(url) as ws:
                await ws.send(subscribe)
                logger.info("CLOB WS connected: %d tokens subscribed", len(token_ids))
                WS_CONNECTS.inc()
                WS_UP.set(1)
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, list):
                        for item in payload:
                            yield item
                    elif isinstance(payload, dict):
                        yield payload
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect on any transport/protocol error
            WS_DROPS.inc()
            WS_UP.set(0)
            logger.warning("CLOB WS dropped (%r); reconnecting in %ss", exc, reconnect_delay_s)
            if on_drop is not None:
                try:
                    await on_drop(exc)
                except Exception:  # noqa: BLE001 - alerting must never kill the stream
                    logger.exception("on_drop handler failed")
            if max_reconnects is not None and reconnects >= max_reconnects:
                return
            reconnects += 1
            await sleep(reconnect_delay_s)


async def run_stream_forever() -> None:
    """Entry point: load the tracked universe, open Redis + the live WS, run the seam."""
    import redis.asyncio as aioredis

    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        universe = await store_mod.load_tracked_markets(session)
    book_store = BookStore.from_markets(universe)
    if not book_store.token_ids:
        logger.warning("no tracked markets; nothing to stream")
        return

    redis = aioredis.from_url(settings.redis_url)
    params = arb_params(settings)

    async def on_drop(_exc: Exception) -> None:
        alert = evaluate_ws_drop(
            drops=1, window_drops_threshold=settings.ws_drop_alert_threshold, now=_utcnow()
        )
        if alert is not None:
            await publish_alert(redis, settings.alerts_channel, alert)

    messages = connect_clob_ws(settings.clob_ws_url, book_store.token_ids, on_drop=on_drop)

    async def publish(advised: AdvisedSignal) -> None:
        await publish_signal(redis, settings.signals_channel, advised)

    logger.info("streaming arb for %d markets", len(universe))
    try:
        await run_stream(
            messages,
            book_store,
            params,
            publish=publish,
            bankroll=settings.backtest_initial_bankroll,
        )
    finally:
        await redis.aclose()


# --- Dev: synthetic publisher (demo the dashboard live-update sans a real crossed book) ------


def _mock_advised(market_id: str, net_edge: Decimal, *, now: Callable[[], datetime]) -> AdvisedSignal:
    """A synthetic set-arb AdvisedSignal for the --mock demo (same wire shape as a real one)."""
    return AdvisedSignal(
        id=_signal_id(market_id),
        time=now(),
        market_id=market_id,
        condition_id=f"0xmock-{market_id}",
        market_question=f"Mock market {market_id}",
        strategy="set_arb",
        kind="long_set",
        market_price=Decimal("1.00") - net_edge,
        p=None,
        edge=net_edge + Decimal("0.02"),
        net_edge=net_edge,
        recommended_size_usd=Decimal(0),
        recommended_size_pct=Decimal(0),
        confidence=Decimal(1),
        gate_passed=True,
        gate=None,
    )


async def run_mock_forever(
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    interval_s: float = 1.0,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Publish drifting synthetic arb signals to Redis on a timer (dev only)."""
    import redis.asyncio as aioredis

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url)
    markets = ["mock-1", "mock-2", "mock-3"]
    logger.info("mock publisher -> %s on %s", settings.signals_channel, settings.redis_url)
    tick = 0
    try:
        while True:
            market_id = markets[tick % len(markets)]
            # net edge drifts 0.02..0.06 so rows visibly change.
            net = Decimal("0.02") + (Decimal(tick % 5) * Decimal("0.01"))
            advised = _mock_advised(market_id, net, now=now)
            await publish_signal(redis, settings.signals_channel, advised)
            logger.info("published mock %s net=%s", market_id, net)
            tick += 1
            await sleep(interval_s)
    finally:
        await redis.aclose()


async def run_mock_drop_forever(
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    interval_s: float = 3.0,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Publish a synthetic ws_drop alert on a timer (dev only) — no real socket needed.

    Exercises the full alert path end-to-end (``evaluate_ws_drop`` -> ``publish_alert`` ->
    Redis + Sentry), so a dev can watch a forced "something broke" event reach the dashboard.
    """
    import redis.asyncio as aioredis

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url)
    logger.info("mock ws-drop publisher -> %s on %s", settings.alerts_channel, settings.redis_url)
    try:
        while True:
            WS_DROPS.inc()
            WS_UP.set(0)
            alert = evaluate_ws_drop(
                drops=1, window_drops_threshold=settings.ws_drop_alert_threshold, now=now()
            )
            if alert is not None:
                await publish_alert(redis, settings.alerts_channel, alert)
                logger.warning("published mock ws_drop alert")
            await sleep(interval_s)
    finally:
        await redis.aclose()


def main() -> None:
    """CLI: ``python -m app.streaming.engine`` (live), ``--mock`` (synthetic signal feed), or
    ``--mock-drop`` (synthetic WS-drop alerts)."""
    configure_logging("quant.streaming")
    init_sentry("quant.streaming")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--mock":
        asyncio.run(run_mock_forever())
    elif mode == "--mock-drop":
        asyncio.run(run_mock_drop_forever())
    else:
        asyncio.run(run_stream_forever())


if __name__ == "__main__":
    main()
