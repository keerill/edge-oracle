"""Paper-trading performance models — the no-money track record of the live advisor.

Computed from the settled ``paper_trades`` (the bets the system *would* have placed, scored
against real outcomes). Mirrors the backtest's metric shape (return / hit-rate / drawdown /
Sharpe-like) so the live paper record and the historical replay are read the same way.

``arb_fill_assumed`` flags the honesty caveat carried from settlement: set-arb paper P&L
assumes the dislocation was still fillable at the advised VWAP (no latency/fill-quality check
yet), so the per-strategy breakdown is where the outcome-verified directional track is read
separately from the fill-optimistic arb track.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.backtest import EquityPoint


class PaperStrategyPerf(BaseModel):
    """Per-strategy slice of the paper record."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    n: int
    wins: int
    hit_rate: Decimal | None
    total_pnl: Decimal
    avg_return: Decimal | None  # mean per-bet pnl/stake
    sharpe_like: Decimal | None


class ArbFillSummary(BaseModel):
    """How the set-arb fill re-check is performing — the trust signal for the arb track.

    Spans set-arb trades of every status (a verified arb is captured ``open`` and may later
    close; a failed one is ``expired``), since the verdict is set at capture, not settlement.
    Arbs skipped on a fetch error are never persisted, so they sit outside ``checked``."""

    model_config = ConfigDict(frozen=True)

    checked: int  # verified + expired (arbs that received a fill verdict)
    verified: int  # fill_ok is True (edge survived the latency gap)
    expired: int  # fill_ok is False (edge vanished -> captured expired)
    survival_rate: Decimal | None  # verified / checked (None when checked == 0)
    avg_latency_s: Decimal | None  # mean fill_latency_s over checked arbs (None when 0)


class PaperPerformance(BaseModel):
    """The paper-trading scorecard: realized P&L of the advisor's recommendations against real
    outcomes, plus the per-strategy breakdown and the realized equity curve."""

    model_config = ConfigDict(frozen=True)

    initial_bankroll: Decimal
    final_bankroll: Decimal
    total_pnl: Decimal
    total_return: Decimal
    hit_rate: Decimal | None
    max_drawdown: Decimal
    sharpe_like: Decimal | None
    n_closed: int
    n_open: int
    per_strategy: dict[str, PaperStrategyPerf]
    equity_curve: tuple[EquityPoint, ...]
    arb_fill_assumed: bool  # caveat: a settled set-arb predates / failed the fill-check
    arb_fill: ArbFillSummary  # how the fill re-check is performing (zeros when no arbs)
