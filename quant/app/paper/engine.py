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
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.config import effective_config
from app.api.signals import _enrich, effective_kelly_frac
from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store
from app.math.arb import ArbParams
from app.models.advisor import AdvisedSignal
from app.models.market import Market
from app.models.paper_trade import PaperTrade
from app.observability.logging import configure_logging
from app.observability.metrics import start_metrics_server
from app.observability.sentry import init_sentry
from app.paper.capture import select_new_paper_trades
from app.paper.fill_check import check_arb_fill
from app.polymarket.clob_client import ClobClient
from app.polymarket.http import make_http_client
from app.signals.engine import fetch_books

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _arb_params(settings: Settings) -> ArbParams:
    """The same set-arb knobs the scanner uses (keeps the re-check identical to detection)."""
    return ArbParams(
        set_size=settings.arb_set_size,
        gas=settings.arb_gas,
        slippage=settings.arb_slippage,
        min_net_edge=settings.arb_min_net_edge,
    )


@dataclass(frozen=True)
class PaperCaptureResult:
    signals: int
    captured: int
    arb_verified: int = 0  # set-arbs whose edge survived the fill re-check (captured open)
    arb_expired: int = 0  # set-arbs whose edge vanished in the latency window (captured expired)


async def _recheck_arb_fills(
    trades: list[PaperTrade],
    advised_by_id: dict[str, AdvisedSignal],
    markets_by_id: dict[str, Market],
    settings: Settings,
    *,
    clob: ClobClient | None,
    sem: asyncio.Semaphore,
    now: Callable[[], datetime],
) -> tuple[list[PaperTrade], int, int]:
    """Re-price each set-arb on a *fresh* book before trusting it (the gating item).

    Directional trades pass through untouched. A passing re-check keeps the arb ``open`` and
    records ``rechecked_net_edge`` (the verified fillable edge); a failing one is captured as
    ``status="expired"`` so it never inflates P&L. An arb we can't re-fetch this cycle (no CLOB
    client, untracked market, or a missing leg) is skipped — its key stays free to retry next
    cycle. Returns ``(trades_to_insert, verified, expired)``."""
    if not settings.arb_fill_check_enabled:
        return trades, 0, 0
    params = _arb_params(settings)
    out: list[PaperTrade] = []
    verified = expired = 0
    for pt in trades:
        if pt.side != "set":
            out.append(pt)
            continue
        advised = advised_by_id.get(pt.id)
        market = markets_by_id.get(pt.market_id)
        if clob is None or advised is None or market is None:
            logger.warning("arb fill-check: cannot re-check %s; skipping this cycle", pt.id)
            continue
        yes_book, no_book = await fetch_books(clob, sem, market)
        if yes_book is None or no_book is None:
            logger.warning("arb fill-check: missing a leg for %s; will retry next cycle", pt.id)
            continue
        checked_at = now()
        verdict = check_arb_fill(
            advised_kind=advised.kind,  # "long_set" / "short_set"
            yes_book=yes_book,
            no_book=no_book,
            params=params,
            advised_at=pt.advised_at,
            checked_at=checked_at,
        )
        update: dict = {
            "fill_checked_at": checked_at,
            "fill_ok": verdict.ok,
            "fill_latency_s": verdict.latency_s,
            "fill_reason": verdict.reason,
        }
        if verdict.ok:
            update["rechecked_net_edge"] = verdict.rechecked_net_edge
            verified += 1
        else:
            update["status"] = "expired"
            expired += 1
        out.append(pt.model_copy(update=update))
    return out, verified, expired


async def run_paper_capture_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    clob: ClobClient | None = None,
    sem: asyncio.Semaphore | None = None,
    limit: int = 200,
    now: Callable[[], datetime] = _utcnow,
) -> PaperCaptureResult:
    """One capture cycle: enrich the recent signals exactly as the advisor does, fill-check the
    set-arbs on a fresh book, then log the new actionable recommendations to ``paper_trades``
    (one open per strategy+market). The seam. ``clob`` supplies the fresh books for the arb
    re-check (the poller injects a live client; tests inject a fake)."""
    sem = sem or asyncio.Semaphore(settings.max_concurrency)
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
        advised_by_id = {a.id: a for a in advised}
        checked, verified, expired = await _recheck_arb_fills(
            new, advised_by_id, markets_by_id, settings, clob=clob, sem=sem, now=now
        )
        n = await store.insert_paper_trades(session, checked)
        await session.commit()

    return PaperCaptureResult(
        signals=len(signals), captured=n, arb_verified=verified, arb_expired=expired
    )


async def run_paper_poller(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the capture loop forever (or ``max_cycles`` times, for tests). Each cycle opens a
    CLOB client for the set-arb fill re-check."""
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            async with make_http_client(settings) as http:
                clob = ClobClient(http, settings)
                result = await run_paper_capture_once(sessionmaker, settings, clob=clob)
            logger.info(
                "paper capture complete: signals=%d captured=%d (arb verified=%d expired=%d)",
                result.signals,
                result.captured,
                result.arb_verified,
                result.arb_expired,
            )
        except Exception as exc:  # noqa: BLE001 - never let one cycle kill the loop
            logger.exception("paper capture cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def _run_once() -> None:
    settings = get_settings()
    async with make_http_client(settings) as http:
        clob = ClobClient(http, settings)
        result = await run_paper_capture_once(get_sessionmaker(), settings, clob=clob)
    logger.info(
        "one-shot paper capture: signals=%d captured=%d (arb verified=%d expired=%d)",
        result.signals,
        result.captured,
        result.arb_verified,
        result.arb_expired,
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
