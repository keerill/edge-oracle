"""The paper-capture engine: log the bets the advisor would place, on a loop.

Reuses the exact live advisor read path (``app.api.signals._enrich`` -> ``advise``) so the
paper journal is sized identically to what the dashboard shows and what a human would act on
— same bankroll, same calibration-shrunk Kelly fraction, same cost gate. It then logs the new
actionable recommendations (deduped per strategy+market) to ``paper_trades``. No money, no
execution: this is the no-money validation feed.

``run_paper_capture_once`` is the timing-free, injectable test seam.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.config import effective_config
from app.api.signals import _enrich, effective_kelly_frac
from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store
from app.observability.logging import configure_logging
from app.observability.metrics import start_metrics_server
from app.observability.sentry import init_sentry
from app.paper.capture import select_new_paper_trades

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperCaptureResult:
    signals: int
    captured: int


async def run_paper_capture_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    limit: int = 200,
) -> PaperCaptureResult:
    """One capture cycle: enrich the recent signals exactly as the advisor does, then log the
    new actionable recommendations to ``paper_trades`` (one open per strategy+market). The seam.
    """
    async with sessionmaker() as session:
        cfg = await effective_config(session, settings)
        frac = await effective_kelly_frac(session, cfg.kelly_frac)
        signals = await store.load_signals(session, limit=limit)
        markets = await store.load_tracked_markets(session)
        markets_by_id = {m.market_id: m for m in markets}
        token_ids = [tid for m in markets for tid in (m.yes_token_id, m.no_token_id)]
        quotes_by_token = await store.load_latest_quotes(session, token_ids=token_ids or None)
        open_keys = {
            (pt.strategy, pt.condition_id)
            for pt in await store.load_paper_trades(session, status="open")
        }

        advised = [
            _enrich(s, markets_by_id, quotes_by_token, settings, cfg.bankroll, frac, cfg.kelly_cap)
            for s in signals
        ]
        new = select_new_paper_trades(advised, already_open=open_keys)
        n = await store.insert_paper_trades(session, new)
        await session.commit()

    return PaperCaptureResult(signals=len(signals), captured=n)


async def run_paper_poller(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the capture loop forever (or ``max_cycles`` times, for tests)."""
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            result = await run_paper_capture_once(sessionmaker, settings)
            logger.info(
                "paper capture complete: signals=%d captured=%d",
                result.signals,
                result.captured,
            )
        except Exception as exc:  # noqa: BLE001 - never let one cycle kill the loop
            logger.exception("paper capture cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def _run_once() -> None:
    settings = get_settings()
    result = await run_paper_capture_once(get_sessionmaker(), settings)
    logger.info(
        "one-shot paper capture: signals=%d captured=%d", result.signals, result.captured
    )


def main() -> None:
    """CLI: ``python -m app.paper.engine`` (one cycle) or ``... loop`` (forever)."""
    configure_logging("quant.paper")
    init_sentry("quant.paper")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        asyncio.run(run_paper_poller(get_sessionmaker(), settings))
    else:
        asyncio.run(_run_once())


if __name__ == "__main__":
    main()
