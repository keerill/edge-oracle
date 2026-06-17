"""Trade-print ingestion: poll the Data API ``/trades`` per tracked market and persist.

A standalone poller (mirrors ``signals.engine``): reloads the tracked universe, fetches each
market's recent trades by condition id, maps each print to its market, and appends to the
``trades`` hypertable. Per-market isolation — one bad market never kills the tick.

Cross-cycle de-duplication uses an injected ``since`` cursor (token -> last seen trade time):
the poller threads one cursor across cycles so re-fetched recent trades aren't re-inserted.
Restart-durable high-water + same-second dedup is a carry-forward (the table is append-only
reference data; ``trade_id`` is a tx hash and not unique per fill).

``run_trades_scan_once`` is the timing-free, injectable test seam.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store
from app.ingestion.trades_transform import trade_from_raw
from app.models.market import Market
from app.models.trade import Trade
from app.observability.logging import configure_logging
from app.observability.metrics import start_metrics_server
from app.observability.sentry import init_sentry
from app.polymarket.data_client import DataClient
from app.polymarket.http import make_http_client

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class TradeScanResult:
    markets: int
    trades: int


async def _trades_for_market(
    data: DataClient, sem: asyncio.Semaphore, market: Market, *, limit: int | None
) -> list[Trade]:
    """Fetch + map one market's trade prints. Per-market isolation: a failed/malformed
    response is logged and yields no trades. Prints for tokens not in this market are
    dropped defensively (the Data API returns this market's tokens only)."""
    own_tokens = {market.yes_token_id, market.no_token_id}
    try:
        async with sem:
            raw_trades = await data.get_trades(condition_id=market.condition_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 - poller must survive bad upstream data
        logger.warning("skipping trades for market %s: %r", market.market_id, exc)
        return []
    out: list[Trade] = []
    for raw in raw_trades:
        if raw.asset not in own_tokens:
            continue
        out.append(trade_from_raw(raw, market_id=market.market_id))
    return out


async def run_trades_scan_once(
    data: DataClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    since: dict[str, datetime] | None = None,
    now: Callable[[], datetime] = _utcnow,  # reserved for symmetry; trades carry their own time
) -> TradeScanResult:
    """One trade-ingestion cycle (timing-free). Reload the universe, fetch each market's
    trades concurrently, drop any already seen per the ``since`` cursor (updated in place),
    and batch-insert the rest. The test seam."""
    async with sessionmaker() as session:
        universe = await store.load_tracked_markets(session)

    sem = asyncio.Semaphore(settings.max_concurrency)
    results = await asyncio.gather(
        *(_trades_for_market(data, sem, m, limit=settings.trades_limit) for m in universe),
        return_exceptions=True,
    )

    fresh: list[Trade] = []
    for r in results:
        if isinstance(r, Exception):  # per-market isolation (belt-and-suspenders)
            logger.warning("market trade-fetch task failed: %r", r)
            continue
        for t in r:
            if since is not None and t.token_id in since and t.time <= since[t.token_id]:
                continue  # already ingested in a prior cycle
            fresh.append(t)

    if since is not None:  # advance the high-water cursor per token
        for t in fresh:
            prev = since.get(t.token_id)
            if prev is None or t.time > prev:
                since[t.token_id] = t.time

    async with sessionmaker() as session:
        n = await store.insert_trades(session, fresh)
        await session.commit()

    return TradeScanResult(markets=len(universe), trades=n)


async def run_trades_poller(
    data: DataClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the trade-ingestion loop forever (or ``max_cycles`` times, for tests). One ``since``
    cursor is threaded across cycles so re-fetched recent trades aren't re-inserted."""
    since: dict[str, datetime] = {}
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            result = await run_trades_scan_once(
                data, sessionmaker, settings, since=since, now=now
            )
            logger.info(
                "trade scan complete: markets=%d trades=%d", result.markets, result.trades
            )
        except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("trade scan cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def _run_once() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        data = DataClient(http, settings)
        result = await run_trades_scan_once(data, sessionmaker, settings)
    logger.info("one-shot trade scan: markets=%d trades=%d", result.markets, result.trades)


async def _run_forever() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        data = DataClient(http, settings)
        await run_trades_poller(data, sessionmaker, settings)


def main() -> None:
    """CLI: ``python -m app.ingestion.trades_engine`` (one cycle) or ``... loop`` (forever)."""
    configure_logging("quant.trades")
    init_sentry("quant.trades")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        asyncio.run(_run_forever())
    else:
        asyncio.run(_run_once())


if __name__ == "__main__":
    main()
