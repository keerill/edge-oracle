"""The signal engine: scan the stored universe for set-arb opportunities + persist.

A *standalone* scanner — deliberately not wired into the ingestion poller. It reloads
the tracked universe, fetches each market's YES/NO books itself, runs the pure arb math
(``app.math.arb``), and writes flagged opportunities to the ``signals`` table. Decisions
live in the pure math; this stays thin. Failures are isolated per token and per market
so one bad book never kills a tick, and one bad tick never kills the loop.

``run_signal_scan_once`` is the timing-free, injectable test seam.
(A future optimization is to consume the full books the ingestion scanner already
fetches, rather than re-fetching here.)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store, transform
from app.math.arb import ArbParams, evaluate_market
from app.models.book import OrderBook
from app.models.market import Market
from app.models.signal import ArbSignal
from app.observability.logging import configure_logging
from app.observability.metrics import SIGNALS, start_metrics_server
from app.observability.sentry import init_sentry
from app.polymarket.clob_client import ClobClient
from app.polymarket.http import make_http_client

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class SignalScanResult:
    markets: int
    signals: int


def _params(settings: Settings) -> ArbParams:
    """Map the EDGE_-prefixed money knobs onto the pure ArbParams (keeps math pure)."""
    return ArbParams(
        set_size=settings.arb_set_size,
        gas=settings.arb_gas,
        slippage=settings.arb_slippage,
        min_net_edge=settings.arb_min_net_edge,
    )


async def fetch_books(
    clob: ClobClient, sem: asyncio.Semaphore, market: Market
) -> tuple[OrderBook | None, OrderBook | None]:
    """Fetch both tokens' books. Per-token isolation: a malformed/failed book for one
    token is logged and returned as ``None`` — the set can't be priced without both legs."""

    async def one(token_id: str) -> OrderBook | None:
        try:
            async with sem:
                raw = await clob.get_book(token_id)
            return transform.orderbook_from_raw(raw, token_id)
        except Exception as exc:  # noqa: BLE001 - scanner must survive bad upstream data
            logger.warning(
                "skipping token %s of market %s: %r", token_id, market.market_id, exc
            )
            return None

    yes_book = await one(market.yes_token_id)
    no_book = await one(market.no_token_id)
    return yes_book, no_book


async def evaluate_one(
    clob: ClobClient,
    sem: asyncio.Semaphore,
    market: Market,
    params: ArbParams,
    at: datetime,
) -> ArbSignal | None:
    """Fetch a market's books and run the set-arb check. ``None`` if a leg is missing
    or there is no profitable opportunity."""
    yes_book, no_book = await fetch_books(clob, sem, market)
    if yes_book is None or no_book is None:
        return None
    return evaluate_market(
        yes_book,
        no_book,
        params,
        market_id=market.market_id,
        condition_id=market.condition_id,
        at=at,
    )


async def run_signal_scan_once(
    clob: ClobClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> SignalScanResult:
    """One signal-scan cycle (timing-free): reload the tracked universe, evaluate each
    market's set-arb concurrently, persist the flagged opportunities. The test seam."""
    at = now()
    async with sessionmaker() as session:
        universe = await store.load_tracked_markets(session)

    params = _params(settings)
    sem = asyncio.Semaphore(settings.max_concurrency)
    results = await asyncio.gather(
        *(evaluate_one(clob, sem, m, params, at) for m in universe),
        return_exceptions=True,
    )
    signals: list[ArbSignal] = []
    for r in results:
        if isinstance(r, Exception):  # per-market isolation (belt-and-suspenders)
            logger.warning("market arb-eval task failed: %r", r)
            continue
        if r is not None:
            signals.append(r)

    async with sessionmaker() as session:
        n = await store.insert_signals(session, signals)
        await session.commit()

    return SignalScanResult(markets=len(universe), signals=n)


async def run_signal_poller(
    clob: ClobClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the signal-scan loop forever (or ``max_cycles`` times, for tests)."""
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            result = await run_signal_scan_once(clob, sessionmaker, settings, now=now)
            SIGNALS.labels("set_arb", "scan").inc(result.signals)
            logger.info(
                "signal scan complete: markets=%d signals=%d",
                result.markets,
                result.signals,
            )
        except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("signal scan cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def run_signal_poller_forever() -> None:
    """Entry point: build deps from settings + loop (reuses ``scan_interval_s``)."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        clob = ClobClient(http, settings)
        await run_signal_poller(clob, sessionmaker, settings)


async def _run_once() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        clob = ClobClient(http, settings)
        result = await run_signal_scan_once(clob, sessionmaker, settings)
    logger.info(
        "one-shot signal scan: markets=%d signals=%d", result.markets, result.signals
    )


def main() -> None:
    """CLI: ``python -m app.signals.engine`` (one cycle) or ``... loop`` (forever)."""
    configure_logging("quant.signals")
    init_sentry("quant.signals")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        asyncio.run(run_signal_poller_forever())
    else:
        asyncio.run(_run_once())


if __name__ == "__main__":
    main()
