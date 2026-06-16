"""Backtest harness — pure, offline, deterministic.

Hand-computed ``Decimal`` worked examples for the payoff, the metric helpers, the
event-driven ``simulate`` bankroll loop, and the Monte-Carlo resampler. Every number is
checked by hand; results are exact ``==`` (quantized only where a division doesn't
terminate). No float ever touches the bankroll arithmetic — the only float is the
Monte-Carlo Gaussian perturbation, and it only decides a 0/1 outcome, never a dollar amount.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.math.backtest import (
    hit_rate,
    max_drawdown,
    realized_pnl,
    sharpe_like,
    simulate,
    total_return,
)
from app.models.backtest import BacktestParams, BetCandidate, ClosedBet

Q2 = Decimal("0.01")
Q6 = Decimal("0.000001")

T0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def _t(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def _directional(
    *,
    market_id="m",
    condition_id="c",
    strategy="extreme_correction",
    tag="t",
    entry=0,
    resolve=1,
    side="yes",
    m_side="0.25",
    p_yes="0.40",
    p_side="0.40",
    p_lo_side="0.35",
    half_spread="0",
    slippage="0",
    gas="0",
) -> BetCandidate:
    return BetCandidate(
        entry_time=_t(entry),
        resolve_time=_t(resolve),
        market_id=market_id,
        condition_id=condition_id,
        strategy=strategy,
        tag=tag,
        kind="directional",
        side=side,
        m_side=Decimal(m_side),
        p_yes=Decimal(p_yes),
        p_side=Decimal(p_side),
        p_lo_side=Decimal(p_lo_side),
        half_spread=Decimal(half_spread),
        slippage=Decimal(slippage),
        gas=Decimal(gas),
    )


def _arb(
    *,
    market_id="ma",
    condition_id="ca",
    tag="ta",
    entry=0,
    resolve=1,
    locked_net_edge="0.03",
    set_size="10",
    capital="9.5",
) -> BetCandidate:
    return BetCandidate(
        entry_time=_t(entry),
        resolve_time=_t(resolve),
        market_id=market_id,
        condition_id=condition_id,
        strategy="set_arb",
        tag=tag,
        kind="arb",
        locked_net_edge=Decimal(locked_net_edge),
        set_size=Decimal(set_size),
        capital=Decimal(capital),
    )


def _closed(strategy: str, stake: str, pnl: str) -> ClosedBet:
    p = Decimal(pnl)
    return ClosedBet(
        entry_time=T0,
        resolve_time=_t(1),
        market_id="m",
        condition_id="c",
        strategy=strategy,
        tag="t",
        stake=Decimal(stake),
        pnl=p,
        won=p > 0,
    )


# --------------------------------------------------------------------------- realized_pnl


def test_realized_pnl_directional_win_zero_cost():
    # Buy YES at 0.25, $50 stake -> 200 shares; YES wins -> 200 - 50 = 150.
    c = _directional()
    assert realized_pnl(c, Decimal("50"), outcome=1) == Decimal("150")


def test_realized_pnl_directional_loss():
    # YES loses -> lose the whole stake.
    c = _directional()
    assert realized_pnl(c, Decimal("50"), outcome=0) == Decimal("-50")


def test_realized_pnl_buy_no_wins_when_outcome_zero():
    # Buy NO at 0.30, $30 -> 100 shares; outcome=0 (NO wins) -> 100 - 30 = 70.
    c = _directional(side="no", m_side="0.30")
    assert realized_pnl(c, Decimal("30"), outcome=0) == Decimal("70")
    # outcome=1 (YES wins) -> the NO bet loses its stake.
    assert realized_pnl(c, Decimal("30"), outcome=1) == Decimal("-30")


def test_realized_pnl_bakes_all_costs_into_the_fill_price():
    # Same $30 win, but half_spread+slippage+gas widen the effective fill 0.25 -> 0.30,
    # so 120 shares (zero-cost) collapses to 100 shares: pnl 90 -> 70. Costs ARE in the result.
    free = _directional(p_side="0.60", p_lo_side="0.55")
    costed = _directional(
        p_side="0.60", p_lo_side="0.55", half_spread="0.02", slippage="0.01", gas="0.02"
    )
    assert realized_pnl(free, Decimal("30"), outcome=1) == Decimal("90")
    assert realized_pnl(costed, Decimal("30"), outcome=1) == Decimal("70")


def test_realized_pnl_arb_is_outcome_independent():
    # Locked edge 0.03 over 10 sets = 0.30, no matter how the market resolves.
    c = _arb()
    assert realized_pnl(c, Decimal("9.5"), outcome=0) == Decimal("0.30")
    assert realized_pnl(c, Decimal("9.5"), outcome=1) == Decimal("0.30")


# --------------------------------------------------------------------------- metric helpers


def test_total_return():
    assert total_return(Decimal("1000"), Decimal("1102.5")) == Decimal("0.1025")


def test_max_drawdown_peak_to_trough():
    # 1000 -> 1150 (peak) -> 1102.5: trough drawdown = 47.5 / 1150.
    dd = max_drawdown([Decimal("1000"), Decimal("1150"), Decimal("1102.5")])
    assert dd.quantize(Q6) == (Decimal("47.5") / Decimal("1150")).quantize(Q6)


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown([Decimal("1000"), Decimal("1100"), Decimal("1200")]) == Decimal("0")


def test_hit_rate():
    closed = [_closed("s", "50", "150"), _closed("s", "47.5", "-47.5")]
    assert hit_rate(closed) == Decimal("0.5")
    assert hit_rate([]) is None


def test_sharpe_like_mean_over_stdev():
    # returns [3.0, -1.0]: mean 1.0, population stdev 2.0 -> 0.5.
    assert sharpe_like([Decimal("3"), Decimal("-1")]) == Decimal("0.5")


def test_sharpe_like_needs_two_points_and_nonzero_spread():
    assert sharpe_like([Decimal("3")]) is None
    assert sharpe_like([Decimal("2"), Decimal("2")]) is None  # zero stdev


# --------------------------------------------------------------------------- simulate

PARAMS = BacktestParams()  # initial 1000, frac 0.25, cap 0.05, corr_cap_frac 0.05


def test_simulate_known_answer_win_then_loss_with_overlap():
    # A: bet $50 at entry (cash 1000), wins +150, resolves at t2.
    # B: enters at t1 while A is still open -> sized off cash 902.5 -> $47.5, loses -47.5, t3.
    # Distinct tags so the correlation cap never binds.
    a = _directional(market_id="m1", condition_id="c1", tag="A", entry=0, resolve=2)
    b = _directional(market_id="m2", condition_id="c2", tag="B", entry=1, resolve=3)
    res = simulate([a, b], {"c1": 1, "c2": 0}, PARAMS)

    assert res.n_bets == 2
    assert res.final_bankroll == Decimal("1102.5")
    assert res.total_return == Decimal("0.1025")
    assert res.hit_rate == Decimal("0.5")
    assert res.sharpe_like == Decimal("0.5")
    # peak 1150 (after A) -> 1102.5 (after B): drawdown 47.5/1150.
    assert res.max_drawdown.quantize(Q6) == (Decimal("47.5") / Decimal("1150")).quantize(Q6)
    # the two stakes the sim actually committed
    stakes = {b.condition_id: b.stake for b in res.closed_bets}
    assert stakes == {"c1": Decimal("50"), "c2": Decimal("47.5")}
    # single-strategy breakdown
    bd = res.per_strategy["extreme_correction"]
    assert (bd.n, bd.wins, bd.total_pnl, bd.total_return) == (2, 1, Decimal("102.5"), Decimal("0.1025"))


def test_simulate_risk_free_arb_pays_regardless_of_outcome():
    res = simulate([_arb()], {"ca": 0}, PARAMS)
    assert res.n_bets == 1
    assert res.final_bankroll == Decimal("1000.30")  # +0.03 * 10 sets, capital returned
    assert res.total_return == Decimal("0.0003")
    assert res.hit_rate == Decimal("1")
    assert res.max_drawdown == Decimal("0")
    assert res.sharpe_like is None  # one bet


def test_simulate_skips_a_bet_that_fails_the_edge_gate():
    # p_lo (0.50) does NOT strictly exceed the all-in cost (m 0.50) -> no stake, no bet.
    gated = _directional(m_side="0.50", p_side="0.55", p_lo_side="0.50")
    res = simulate([gated], {"c": 0}, PARAMS)
    assert res.n_bets == 0
    assert res.final_bankroll == Decimal("1000")
    assert res.total_return == Decimal("0")
    assert res.hit_rate is None
    assert res.max_drawdown == Decimal("0")
    assert res.closed_bets == ()


def test_simulate_correlation_cap_clamps_a_same_tag_bet():
    # corr_cap_frac 0.10. E takes $50 on tag 'macro'. F would size $47.5 but the tag
    # ceiling at entry (0.10 * 950 = 95) minus E's open 50 leaves only 45 -> clamped to 45.
    params = BacktestParams(corr_cap_frac=Decimal("0.10"))
    e = _directional(market_id="m1", condition_id="c1", tag="macro", entry=0, resolve=3)
    f = _directional(market_id="m2", condition_id="c2", tag="macro", entry=1, resolve=2)
    res = simulate([e, f], {"c1": 1, "c2": 1}, params)
    stakes = {b.condition_id: b.stake for b in res.closed_bets}
    assert stakes["c1"] == Decimal("50")
    assert stakes["c2"] == Decimal("45")  # clamped from 47.5 by the per-tag cap


def test_simulate_no_look_ahead_future_outcome_cannot_change_earlier_stakes():
    # B enters at t1, before A resolves at t2. Flipping A's (later) outcome must not move
    # B's stake — sizing at entry can only see resolutions strictly before that entry.
    a = _directional(market_id="m1", condition_id="c1", tag="A", entry=0, resolve=2)
    b = _directional(market_id="m2", condition_id="c2", tag="B", entry=1, resolve=3)
    base = simulate([a, b], {"c1": 1, "c2": 0}, PARAMS)
    flipped = simulate([a, b], {"c1": 0, "c2": 0}, PARAMS)  # A now loses

    def stake(res, cid):
        return next(x.stake for x in res.closed_bets if x.condition_id == cid)

    assert stake(base, "c1") == stake(flipped, "c1")  # A unchanged (entered first)
    assert stake(base, "c2") == stake(flipped, "c2")  # B unchanged despite A flipping
