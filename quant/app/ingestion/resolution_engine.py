"""Resolution-watcher: detect resolved tracked markets and journal their predictions.

For each tracked market that has resolved on Gamma, match it to the latest directional
(``extreme_correction``) signal we recorded, and append a ``CalibrationRecord`` (claimed
probability, market price, realized outcome) to the calibration journal. The calibration math
(``app.math.calibration``) then scores it and — wired in ``api/signals`` — shrinks the live Kelly
fraction when the model proves overconfident. Idempotent: a market already in the journal is
skipped, so re-running never double-counts.

``run_resolution_scan_once`` is the timing-free, injectable test seam.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store, transform
from app.ingestion.resolution import calibration_from_resolution, resolved_outcome
from app.math.profit import arb_locked_profit, settled_pnl
from app.models.calibration import CalibrationRecord
from app.models.signal import ExtremeCorrectionSignal
from app.observability.logging import configure_logging
from app.observability.metrics import start_metrics_server
from app.observability.sentry import init_sentry
from app.polymarket.gamma_client import GammaClient
from app.polymarket.http import make_http_client

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class ResolutionScanResult:
    checked: int  # resolved markets seen among the tracked universe
    journaled: int  # new calibration records written


async def run_resolution_scan_once(
    gamma: GammaClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> ResolutionScanResult:
    """One resolution-watcher cycle (timing-free). Find resolved tracked markets, journal the
    directional prediction for each not already recorded. The test seam."""
    at = now()
    async with sessionmaker() as session:
        markets = await store.load_tracked_markets(session)
        # latest directional signal per market (newest-first load -> first wins)
        signals = await store.load_signals(session, strategy="extreme_correction", limit=500)
        already = {r.market_id for r in await store.load_calibration(session, strategy="extreme_correction")}

    market_id_by_condition = {m.condition_id: m.market_id for m in markets}
    latest_signal: dict[str, ExtremeCorrectionSignal] = {}
    for s in signals:
        if isinstance(s, ExtremeCorrectionSignal) and s.market_id not in latest_signal:
            latest_signal[s.market_id] = s

    resolved = await gamma.fetch_resolutions(list(market_id_by_condition))

    records: list[CalibrationRecord] = []
    checked = 0
    for raw in resolved:
        outcomes = transform.parse_stringified_str_array(raw.outcomes)
        prices = transform.parse_stringified_str_array(raw.outcomePrices)
        outcome = resolved_outcome(outcomes, prices)
        if outcome is None:
            continue
        checked += 1
        market_id = market_id_by_condition.get(raw.conditionId)
        if market_id is None or market_id in already:
            continue
        signal = latest_signal.get(market_id)
        if signal is None:  # no directional prediction to score for this market
            continue
        records.append(calibration_from_resolution(signal, outcome=outcome, at=at))

    async with sessionmaker() as session:
        n = await store.insert_calibration(session, records)
        await session.commit()

    return ResolutionScanResult(checked=checked, journaled=n)


@dataclass(frozen=True)
class SettlementResult:
    checked: int  # resolved markets seen with open positions
    settled: int  # positions closed with realized P&L


async def run_position_settlement_once(
    gamma: GammaClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> SettlementResult:
    """One settlement cycle (timing-free). For each open directional position whose market has
    resolved on Gamma, compute realized P&L and close it. Only ``status='open'`` rows are
    settled, so re-runs are idempotent. Arb ('set') positions are skipped (no outcome-dependent
    P&L to realize from a YES/NO resolution)."""
    at = now()
    async with sessionmaker() as session:
        open_positions = await store.load_positions(session, status="open")

    directional = [p for p in open_positions if p.side in ("yes", "no")]
    if not directional:
        return SettlementResult(checked=0, settled=0)

    condition_ids = sorted({p.condition_id for p in directional})
    resolved = await gamma.fetch_resolutions(condition_ids)
    outcome_by_condition: dict[str, int] = {}
    for raw in resolved:
        outcomes = transform.parse_stringified_str_array(raw.outcomes)
        prices = transform.parse_stringified_str_array(raw.outcomePrices)
        outcome = resolved_outcome(outcomes, prices)
        if outcome is not None:
            outcome_by_condition[raw.conditionId] = outcome

    settled = 0
    async with sessionmaker() as session:
        for p in directional:
            outcome = outcome_by_condition.get(p.condition_id)
            if outcome is None:
                continue
            pnl = settled_pnl(p.side, p.stake_usd, p.entry_price, outcome)
            await store.settle_position(
                session, p.id, outcome=outcome, pnl=pnl, resolved_at=at
            )
            settled += 1
        await session.commit()

    return SettlementResult(checked=len(outcome_by_condition), settled=settled)


@dataclass(frozen=True)
class PaperSettlementResult:
    directional_settled: int  # paper trades closed against a real market outcome
    arb_settled: int  # set-arb paper trades closed at their locked (fill-assumed) edge


async def run_paper_settlement_once(
    gamma: GammaClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> PaperSettlementResult:
    """One paper-trade settlement cycle (timing-free) — the no-money P&L scorer.

    Directional ('yes'/'no') paper trades settle against the real market outcome exactly like
    live positions (cost basis = the advised all-in price), so paper P&L matches the backtest.
    Set-arb ('set') paper trades are outcome-independent: they settle immediately at their
    locked ``edge`` (per set).

    Set-arb P&L settles on the **fill-verified** edge (``rechecked_net_edge``, re-priced on a
    fresh book at capture time), falling back to the detection-time ``edge`` for legacy rows
    captured before the fill-check existed. Arbs whose edge did not survive the latency window
    were captured ``status='expired'`` and are skipped here (only ``status='open'`` rows are
    touched, idempotent), so they never inflate P&L."""
    at = now()
    async with sessionmaker() as session:
        open_trades = await store.load_paper_trades(session, status="open")
    if not open_trades:
        return PaperSettlementResult(directional_settled=0, arb_settled=0)

    arb = [pt for pt in open_trades if pt.side == "set"]
    directional = [pt for pt in open_trades if pt.side in ("yes", "no")]

    outcome_by_condition: dict[str, int] = {}
    if directional:
        condition_ids = sorted({pt.condition_id for pt in directional})
        resolved = await gamma.fetch_resolutions(condition_ids)
        for raw in resolved:
            outcomes = transform.parse_stringified_str_array(raw.outcomes)
            prices = transform.parse_stringified_str_array(raw.outcomePrices)
            outcome = resolved_outcome(outcomes, prices)
            if outcome is not None:
                outcome_by_condition[raw.conditionId] = outcome

    arb_settled = 0
    directional_settled = 0
    async with sessionmaker() as session:
        for pt in arb:
            edge = pt.rechecked_net_edge if pt.rechecked_net_edge is not None else pt.edge
            pnl = arb_locked_profit(edge, pt.shares)
            await store.settle_paper_trade(
                session, pt.id, outcome=None, realized_pnl=pnl, resolved_at=at
            )
            arb_settled += 1
        for pt in directional:
            outcome = outcome_by_condition.get(pt.condition_id)
            if outcome is None:
                continue
            pnl = settled_pnl(pt.side, pt.stake_usd, pt.advised_price, outcome)
            await store.settle_paper_trade(
                session, pt.id, outcome=outcome, realized_pnl=pnl, resolved_at=at
            )
            directional_settled += 1
        await session.commit()

    return PaperSettlementResult(
        directional_settled=directional_settled, arb_settled=arb_settled
    )


async def run_resolution_cycle(
    gamma: GammaClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> tuple[ResolutionScanResult, SettlementResult, PaperSettlementResult]:
    """All three resolution-time passes: journal calibration, settle live positions, settle
    paper trades. Run once per loop tick (and as the one-shot CLI)."""
    scan = await run_resolution_scan_once(gamma, sessionmaker, settings, now=now)
    positions = await run_position_settlement_once(gamma, sessionmaker, settings, now=now)
    paper = await run_paper_settlement_once(gamma, sessionmaker, settings, now=now)
    return scan, positions, paper


async def run_resolution_poller(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the resolution cycle forever (or ``max_cycles`` times, for tests). Resolutions are
    slow, so this reuses the discovery cadence rather than the fast scan interval."""
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            async with make_http_client(settings) as http:
                gamma = GammaClient(http, settings)
                scan, positions, paper = await run_resolution_cycle(gamma, sessionmaker, settings)
            logger.info(
                "resolution cycle: journaled=%d positions_settled=%d "
                "paper_settled=%d (directional=%d arb=%d)",
                scan.journaled,
                positions.settled,
                paper.directional_settled + paper.arb_settled,
                paper.directional_settled,
                paper.arb_settled,
            )
        except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("resolution cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.discovery_interval_s)


async def _run_once() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        gamma = GammaClient(http, settings)
        scan, positions, paper = await run_resolution_cycle(gamma, sessionmaker, settings)
    logger.info(
        "one-shot resolution scan: checked=%d journaled=%d positions_settled=%d "
        "paper_settled=%d (directional=%d arb=%d)",
        scan.checked,
        scan.journaled,
        positions.settled,
        paper.directional_settled + paper.arb_settled,
        paper.directional_settled,
        paper.arb_settled,
    )


async def _run_loop() -> None:
    settings = get_settings()
    await run_resolution_poller(get_sessionmaker(), settings)


def main() -> None:
    """CLI: ``python -m app.ingestion.resolution_engine`` (one cycle) or ``... loop`` (forever)."""
    import sys

    configure_logging("quant.resolution")
    init_sentry("quant.resolution")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        asyncio.run(_run_loop())
    else:
        asyncio.run(_run_once())


if __name__ == "__main__":
    main()
