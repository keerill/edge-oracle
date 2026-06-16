"""Pure backtest math — the payoff, the metric helpers, the event-driven bankroll
simulation, and the Monte-Carlo resampler.

Everything here is pure (``Decimal`` in, models/``Decimal`` out — no I/O, no clock, no
``Settings``). Reuses the existing sizing math (:func:`app.math.bet_sizing.position_size`)
unchanged. The only float in the whole module is the Monte-Carlo Gaussian perturbation,
and it only ever decides a 0/1 outcome — never a dollar amount, so the bankroll arithmetic
stays exact ``Decimal``.

Money-math rules baked in (see CLAUDE.md and the slice plan):
  * Directional fill price is all-in: ``m_side + half_spread + slippage + gas`` — every
    cost is in the *realized* P&L, never only in the gate.
  * Arb P&L is the locked ``net_edge * set_size`` (already net of gas + slippage),
    independent of how the market resolves.
  * Stakes go through :func:`position_size` (gate on ``p_lo``, fractional Kelly, hard cap),
    then a streaming per-tag correlation cap.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from decimal import Decimal

from app.math.bet_sizing import position_size
from app.models.backtest import (
    BacktestParams,
    BacktestResult,
    BetCandidate,
    ClosedBet,
    EquityPoint,
    MonteCarloResult,
    StrategyBreakdown,
)

ZERO = Decimal(0)
ONE = Decimal(1)


def realized_pnl(candidate, stake: Decimal, outcome: int) -> Decimal:
    """Realized profit/loss for a resolved bet, with all costs baked in.

    Directional: you buy ``stake`` dollars of the side at the all-in fill price
    ``m_side + half_spread + slippage + gas``; a win pays $1/share, a loss pays $0.
    Arb: the outcome-independent locked edge ``net_edge * set_size``.
    """
    if candidate.kind == "arb":
        return candidate.locked_net_edge * candidate.set_size

    fill = candidate.m_side + candidate.half_spread + candidate.slippage + candidate.gas
    shares = stake / fill
    won = (outcome == 1) if candidate.side == "yes" else (outcome == 0)
    return (shares - stake) if won else (-stake)


def total_return(initial: Decimal, final: Decimal) -> Decimal:
    """Fractional return ``(final - initial) / initial``."""
    return (final - initial) / initial


def max_drawdown(equity_series: Sequence[Decimal]) -> Decimal:
    """Largest peak-to-trough decline of an equity series, as a fraction in ``[0, 1]``.

    ``equity_series`` must start with the opening bankroll. Returns ``0`` for an empty or
    monotonically non-decreasing series.
    """
    peak = None
    worst = ZERO
    for equity in equity_series:
        if peak is None or equity > peak:
            peak = equity
        if peak > ZERO:
            dd = (peak - equity) / peak
            if dd > worst:
                worst = dd
    return worst


def hit_rate(closed: Sequence[ClosedBet]) -> Decimal | None:
    """Fraction of resolved bets that won. ``None`` when there are no bets."""
    if not closed:
        return None
    wins = sum(1 for b in closed if b.won)
    return Decimal(wins) / Decimal(len(closed))


def sharpe_like(returns: Sequence[Decimal]) -> Decimal | None:
    """Mean per-bet return over its population std-dev — an unannualized Sharpe-like
    ratio. ``None`` with fewer than 2 returns or zero dispersion (undefined)."""
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns, ZERO) / Decimal(n)
    var = sum(((r - mean) ** 2 for r in returns), ZERO) / Decimal(n)
    if var == ZERO:
        return None
    return mean / var.sqrt()


def _entry_stake(
    c: BetCandidate, cash: Decimal, exposure_by_tag: Mapping[str, Decimal], params: BacktestParams
) -> Decimal:
    """Dollars to commit to ``c`` at entry, off the currently-available ``cash``.

    Directional bets go through :func:`position_size` (gate + fractional Kelly + cap);
    arb bets deploy their full set cost (risk-free). Both are then clamped by the
    streaming per-tag correlation cap: open exposure for the tag may not exceed
    ``corr_cap_frac * cash`` (the streaming analogue of ``cap_correlated_stakes``).
    """
    max_per_tag = params.corr_cap_frac * cash
    allowed = max(ZERO, max_per_tag - exposure_by_tag.get(c.tag, ZERO))

    if c.kind == "arb":
        if c.locked_net_edge > ZERO and c.capital <= min(cash, allowed):
            return c.capital  # take the whole set or none (no fractional sets)
        return ZERO

    stake = position_size(
        cash,
        c.p_side,
        c.p_lo_side,
        c.m_side,
        c.half_spread,
        c.slippage,
        c.gas,
        params.frac,
        params.cap,
    )
    return min(stake, allowed)


def simulate(
    candidates: Sequence[BetCandidate],
    outcomes: Mapping[str, int],
    params: BacktestParams,
) -> BacktestResult:
    """Replay ``candidates`` as a causal event loop and report performance.

    Two event types, processed in time order with **resolutions before entries** on a tie
    (free capital before deciding new bets): *entry* sizes off the live bankroll and locks
    the stake; *resolution* realizes P&L (``outcomes[condition_id]``), returns the capital,
    and samples realized equity. Sizing at time ``t`` can therefore only see capital freed
    by resolutions strictly before ``t`` — no look-ahead. All costs are baked into the P&L.
    """
    initial = params.initial_bankroll
    cash = initial
    open_pos: dict[int, tuple[BetCandidate, Decimal]] = {}
    exposure_by_tag: dict[str, Decimal] = {}
    closed: list[ClosedBet] = []
    equity_curve: list[EquityPoint] = []

    # (time, phase, index): phase 0 = resolution, 1 = entry. Stable sort keeps candidate
    # order within a (time, phase) tie.
    events: list[tuple] = []
    for i, c in enumerate(candidates):
        events.append((c.entry_time, 1, i))
        events.append((c.resolve_time, 0, i))
    events.sort(key=lambda e: (e[0], e[1]))

    for time, phase, i in events:
        c = candidates[i]
        if phase == 1:  # entry
            stake = _entry_stake(c, cash, exposure_by_tag, params)
            if stake > ZERO:
                cash -= stake
                open_pos[i] = (c, stake)
                exposure_by_tag[c.tag] = exposure_by_tag.get(c.tag, ZERO) + stake
        else:  # resolution
            if i not in open_pos:
                continue  # bet was never taken (gated out or fully clamped)
            c, stake = open_pos.pop(i)
            exposure_by_tag[c.tag] -= stake
            pnl = realized_pnl(c, stake, outcomes[c.condition_id])
            cash += stake + pnl  # return locked capital + realized P&L
            closed.append(
                ClosedBet(
                    entry_time=c.entry_time,
                    resolve_time=c.resolve_time,
                    market_id=c.market_id,
                    condition_id=c.condition_id,
                    strategy=c.strategy,
                    tag=c.tag,
                    stake=stake,
                    pnl=pnl,
                    won=pnl > ZERO,
                )
            )
            locked = sum((s for (_, s) in open_pos.values()), ZERO)
            equity_curve.append(EquityPoint(time=time, equity=cash + locked))

    return _build_result(initial, cash, closed, equity_curve)


def _build_result(
    initial: Decimal,
    final: Decimal,
    closed: Sequence[ClosedBet],
    equity_curve: Sequence[EquityPoint],
) -> BacktestResult:
    returns = [b.pnl / b.stake for b in closed]
    series = [initial, *(p.equity for p in equity_curve)]

    per_strategy: dict[str, StrategyBreakdown] = {}
    by_strat: dict[str, list[ClosedBet]] = {}
    for b in closed:
        by_strat.setdefault(b.strategy, []).append(b)
    for strat, bets in by_strat.items():
        total_pnl = sum((b.pnl for b in bets), ZERO)
        per_strategy[strat] = StrategyBreakdown(
            strategy=strat,
            n=len(bets),
            wins=sum(1 for b in bets if b.won),
            hit_rate=hit_rate(bets),
            total_pnl=total_pnl,
            total_return=total_pnl / initial,
            sharpe_like=sharpe_like([b.pnl / b.stake for b in bets]),
        )

    return BacktestResult(
        initial_bankroll=initial,
        final_bankroll=final,
        total_return=total_return(initial, final),
        hit_rate=hit_rate(closed),
        max_drawdown=max_drawdown(series),
        sharpe_like=sharpe_like(returns),
        n_bets=len(closed),
        per_strategy=per_strategy,
        equity_curve=tuple(equity_curve),
        closed_bets=tuple(closed),
    )


def simulate_with_distribution(
    candidates: Sequence[BetCandidate],
    outcomes: Mapping[str, int],
    params: BacktestParams,
) -> BacktestResult:
    """The deterministic replay PLUS the Monte-Carlo distribution attached.

    This is the heavy path the API serves: :func:`monte_carlo` re-runs :func:`simulate`
    ``mc_sims`` times, so callers that only need the single realized path keep using
    :func:`simulate` directly. ``monte_carlo`` stays ``None`` when there are no candidates —
    a distribution over an empty replay is undefined. Deterministic: ``monte_carlo`` seeds
    its RNG from ``params.mc_seed``, so the same inputs always yield the same distribution.
    """
    result = simulate(candidates, outcomes, params)
    mc = monte_carlo(candidates, params, base_outcomes=outcomes) if candidates else None
    return result.model_copy(update={"monte_carlo": mc})


def _percentile(sorted_vals: Sequence[Decimal], pct: int) -> Decimal:
    """Nearest-rank percentile of an already-sorted, non-empty sequence."""
    idx = min(len(sorted_vals) - 1, (pct * len(sorted_vals)) // 100)
    return sorted_vals[idx]


def monte_carlo(
    candidates: Sequence[BetCandidate],
    params: BacktestParams,
    *,
    base_outcomes: Mapping[str, int] | None = None,
    rng: random.Random | None = None,
) -> MonteCarloResult:
    """Resample outcomes ``mc_sims`` times and report the distribution of final bankroll.

    Each market's YES outcome is drawn ``Bernoulli(clip(p_yes + N(0, mc_sigma), 0, 1))`` —
    the model's own probability *plus* a Gaussian model-error perturbation — then the full
    causal :func:`simulate` is re-run (sizing adapts to each simulated bankroll path).
    Markets with no directional ``p_yes`` (arb-only, where the outcome can't change P&L)
    fall back to ``base_outcomes`` (default 0). ``rng`` may be injected (for tests); when
    omitted it is seeded from ``params.mc_seed`` so determinism follows from the params
    alone. The float perturbation only ever decides a 0/1 outcome, so the bankroll stays
    exact Decimal.
    """
    if rng is None:
        rng = random.Random(params.mc_seed)
    base_outcomes = base_outcomes or {}
    p_by_condition: dict[str, Decimal] = {
        c.condition_id: c.p_yes for c in candidates if c.kind == "directional"
    }
    condition_ids = sorted({c.condition_id for c in candidates})  # deterministic draw order
    sigma = float(params.mc_sigma)

    finals: list[Decimal] = []
    drawdowns: list[Decimal] = []
    for _ in range(params.mc_sims):
        outcomes: dict[str, int] = {}
        for cond in condition_ids:
            p = p_by_condition.get(cond)
            if p is None:
                outcomes[cond] = base_outcomes.get(cond, 0)
                continue
            eff = min(1.0, max(0.0, float(p) + rng.gauss(0.0, sigma)))
            outcomes[cond] = 1 if rng.random() < eff else 0
        res = simulate(candidates, outcomes, params)
        finals.append(res.final_bankroll)
        drawdowns.append(res.max_drawdown)

    finals_sorted = sorted(finals)
    n = len(finals)
    initial = params.initial_bankroll
    losses = sum(1 for f in finals if f < initial)
    return MonteCarloResult(
        n_sims=n,
        final_bankroll_p5=_percentile(finals_sorted, 5),
        final_bankroll_p25=_percentile(finals_sorted, 25),
        final_bankroll_median=_percentile(finals_sorted, 50),
        final_bankroll_p75=_percentile(finals_sorted, 75),
        final_bankroll_p95=_percentile(finals_sorted, 95),
        final_bankroll_mean=sum(finals, ZERO) / Decimal(n),
        median_max_drawdown=_percentile(sorted(drawdowns), 50),
        prob_loss=Decimal(losses) / Decimal(n),
    )
