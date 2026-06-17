"""Price-signal scanner: run the favourite-longshot + extreme-correction heuristics live.

A standalone scanner (mirrors ``signals.engine``) that reads the latest YES midpoint per tracked
market from the stored ``quotes`` and runs the two pure price-signal functions
(``app.math.longshot`` / ``app.math.correction``), persisting any flagged signals. No network: it
consumes the ingestion poller's stored quotes. ``run_price_scan_once`` is the timing-free seam.

Decisions live in the pure math; this stays thin. Each strategy is inserted as its own
homogeneous batch (``insert_signals`` compiles columns from the first row).
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
from app.ingestion import store
from app.math.correction import CorrectionParams, evaluate_extreme_correction
from app.math.longshot import LongshotParams, evaluate_favourite_longshot
from app.models.signal import ExtremeCorrectionSignal, FavouriteLongshotSignal
from app.observability.logging import configure_logging
from app.observability.metrics import SIGNALS, start_metrics_server
from app.observability.sentry import init_sentry

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class PriceScanResult:
    markets: int
    corrections: int
    longshots: int


def _correction_params(s: Settings) -> CorrectionParams:
    return CorrectionParams(
        lo=s.correction_lo, hi=s.correction_hi,
        nudge_min=s.correction_nudge_min, nudge_max=s.correction_nudge_max,
    )


def _longshot_params(s: Settings) -> LongshotParams:
    return LongshotParams(
        longshot_lo=s.longshot_lo, longshot_hi=s.longshot_hi,
        favourite_lo=s.favourite_lo, favourite_hi=s.favourite_hi,
    )


async def run_price_scan_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> PriceScanResult:
    """One price-signal cycle (timing-free): read the latest YES midpoint per tracked market,
    evaluate both heuristics, persist the flagged signals. The test seam."""
    at = now()
    corr_params = _correction_params(settings)
    ls_params = _longshot_params(settings)

    async with sessionmaker() as session:
        markets = await store.load_tracked_markets(session)
        yes_tokens = [m.yes_token_id for m in markets]
        latest = await store.load_latest_quotes(session, token_ids=yes_tokens or None)

    corrections: list[ExtremeCorrectionSignal] = []
    longshots: list[FavouriteLongshotSignal] = []
    for m in markets:
        quote = latest.get(m.yes_token_id)
        if quote is None or quote.midpoint is None:
            continue  # no priced YES midpoint -> nothing to evaluate
        mid = quote.midpoint
        corr = evaluate_extreme_correction(
            mid, corr_params, market_id=m.market_id, condition_id=m.condition_id, at=at
        )
        if corr is not None:
            corrections.append(corr)
        ls = evaluate_favourite_longshot(
            mid, ls_params, market_id=m.market_id, condition_id=m.condition_id, at=at
        )
        if ls is not None:
            longshots.append(ls)

    async with sessionmaker() as session:
        n_corr = await store.insert_signals(session, corrections)
        n_ls = await store.insert_signals(session, longshots)
        await session.commit()

    return PriceScanResult(markets=len(markets), corrections=n_corr, longshots=n_ls)


async def run_price_poller(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the price-signal loop forever (or ``max_cycles`` times, for tests)."""
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            result = await run_price_scan_once(sessionmaker, settings, now=now)
            SIGNALS.labels("extreme_correction", "scan").inc(result.corrections)
            SIGNALS.labels("favourite_longshot", "scan").inc(result.longshots)
            logger.info(
                "price scan complete: markets=%d corrections=%d longshots=%d",
                result.markets, result.corrections, result.longshots,
            )
        except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("price scan cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def _run_once() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    result = await run_price_scan_once(sessionmaker, settings)
    logger.info(
        "one-shot price scan: markets=%d corrections=%d longshots=%d",
        result.markets, result.corrections, result.longshots,
    )


async def _run_forever() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    await run_price_poller(sessionmaker, settings)


def main() -> None:
    """CLI: ``python -m app.signals.price_engine`` (one cycle) or ``... loop`` (forever)."""
    configure_logging("quant.price_signals")
    init_sentry("quant.price_signals")
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
