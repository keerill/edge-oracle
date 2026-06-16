"""Backtest domain models — frozen, ``Decimal``-native.

A ``BetCandidate`` is one time-stamped *entry decision* discovered by replaying the
stored price history; it carries everything needed to size and resolve the bet but **not**
the stake — the stake is decided at entry time off the live bankroll inside ``simulate``.
Directional bets (price signals) and risk-free arb bets share the model; the mutually
exclusive per-kind fields mirror how the ``signals`` table keeps one nullable column set
per strategy. The result models (``ClosedBet``, ``BacktestResult``, ``MonteCarloResult``)
are what the harness reports — all money stays ``Decimal``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

BetSide = Literal["yes", "no"]
BetKind = Literal["directional", "arb"]


class BetCandidate(BaseModel):
    """A sized-later entry decision. ``kind="directional"`` fills the price/probability
    fields (buy one ``side`` at ``m_side``); ``kind="arb"`` fills the locked-edge fields
    (outcome-independent). The other kind's fields stay ``None``."""

    model_config = ConfigDict(frozen=True)

    entry_time: datetime
    resolve_time: datetime
    market_id: str
    condition_id: str
    strategy: str
    tag: str  # macro theme for the correlation cap (one tag, one bet)
    kind: BetKind

    # --- directional (price-signal) fields ---
    side: BetSide | None = None
    m_side: Decimal | None = None  # the side token's price (pre-cost) you pay into
    p_yes: Decimal | None = None  # model P(YES resolves) — used to resample outcomes in MC
    p_side: Decimal | None = None  # model P(this side wins) = p_yes or 1 - p_yes
    p_lo_side: Decimal | None = None  # CI lower bound for the side (gates the bet)
    half_spread: Decimal | None = None
    slippage: Decimal | None = None
    gas: Decimal | None = None

    # --- arb (set-arb) fields ---
    locked_net_edge: Decimal | None = None  # per set, already net of gas + slippage
    set_size: Decimal | None = None
    capital: Decimal | None = None  # dollars locked to hold the set to resolution

    @model_validator(mode="after")
    def _require_fields_for_kind(self) -> BetCandidate:
        if self.resolve_time <= self.entry_time:
            # A bet must be entered strictly before it resolves; otherwise the simulation's
            # (resolution-before-entry on ties) ordering would drop the resolution and lock
            # the stake forever.
            raise ValueError(
                f"resolve_time ({self.resolve_time}) must be after entry_time ({self.entry_time})"
            )
        if self.kind == "directional":
            missing = [
                n
                for n in ("side", "m_side", "p_yes", "p_side", "p_lo_side", "half_spread", "slippage", "gas")
                if getattr(self, n) is None
            ]
            if missing:
                raise ValueError(f"directional candidate missing fields: {missing}")
        else:  # arb
            missing = [
                n for n in ("locked_net_edge", "set_size", "capital") if getattr(self, n) is None
            ]
            if missing:
                raise ValueError(f"arb candidate missing fields: {missing}")
        return self


class MarketResolution(BaseModel):
    """How a market resolved — the explicit outcome input to the backtest (resolution
    ingestion is a later slice). ``outcome`` is 1 if YES resolved true, else 0."""

    model_config = ConfigDict(frozen=True)

    outcome: Literal[0, 1]
    resolve_time: datetime


class ClosedBet(BaseModel):
    """A bet that was taken and has resolved — its realized P&L is final."""

    model_config = ConfigDict(frozen=True)

    entry_time: datetime
    resolve_time: datetime
    market_id: str
    condition_id: str
    strategy: str
    tag: str
    stake: Decimal  # dollars committed at entry
    pnl: Decimal  # realized profit/loss (costs already baked in)
    won: bool  # pnl > 0


class EquityPoint(BaseModel):
    """Realized equity (= initial + cumulative realized P&L) sampled at a resolution."""

    model_config = ConfigDict(frozen=True)

    time: datetime
    equity: Decimal


class StrategyBreakdown(BaseModel):
    """Per-strategy slice of the result."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    n: int
    wins: int
    hit_rate: Decimal | None
    total_pnl: Decimal
    total_return: Decimal  # total_pnl / initial bankroll (contribution to overall return)
    sharpe_like: Decimal | None


class BacktestResult(BaseModel):
    """Everything the deterministic replay reports (costs baked in throughout)."""

    model_config = ConfigDict(frozen=True)

    initial_bankroll: Decimal
    final_bankroll: Decimal
    total_return: Decimal
    hit_rate: Decimal | None
    max_drawdown: Decimal
    sharpe_like: Decimal | None
    n_bets: int
    per_strategy: dict[str, StrategyBreakdown]
    equity_curve: tuple[EquityPoint, ...]
    closed_bets: tuple[ClosedBet, ...]


class MonteCarloResult(BaseModel):
    """Distribution of outcomes over resampled simulations — variance, not just the mean."""

    model_config = ConfigDict(frozen=True)

    n_sims: int
    final_bankroll_p5: Decimal
    final_bankroll_p25: Decimal
    final_bankroll_median: Decimal
    final_bankroll_p75: Decimal
    final_bankroll_p95: Decimal
    final_bankroll_mean: Decimal
    median_max_drawdown: Decimal
    prob_loss: Decimal  # fraction of sims ending below the initial bankroll


class BacktestParams(BaseModel):
    """Pure mirror of the ``EDGE_*`` backtest knobs (no I/O, no ``Settings`` in the math)."""

    model_config = ConfigDict(frozen=True)

    initial_bankroll: Decimal = Decimal(1000)
    frac: Decimal = Decimal("0.25")  # fractional Kelly
    cap: Decimal = Decimal("0.05")  # hard per-position cap (fraction of bankroll)
    corr_cap_frac: Decimal = Decimal("0.05")  # per-tag exposure cap (fraction of bankroll)
    model_error_margin: Decimal = Decimal("0.05")  # p_lo = p_side - this (CI lower bound)
    mc_sigma: Decimal = Decimal("0.05")  # std-dev of the Gaussian model-error perturbation
    mc_sims: int = 1000
    mc_seed: int = 12345
