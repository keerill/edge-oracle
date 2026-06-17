"""Per-bet profit / EV math tests — the dollar-view spec. Sync, offline, deterministic.

Hand-computed ``Decimal`` worked examples, exact ``==`` where the division terminates.
The win/loss payoff is cross-checked against :func:`app.math.backtest.realized_pnl` so the
advisor's "expected earnings" and the backtest's realized P&L can never drift apart.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.math.backtest import realized_pnl
from app.math.profit import (
    arb_locked_profit,
    expected_value,
    mark_to_market,
    prob_of_loss,
    profit_if_loss,
    profit_if_win,
    shares,
)
from app.models.backtest import BetCandidate

# --- shares: stake / ask ------------------------------------------------------


def test_shares_worked_example():
    # $50 at an all-in ask of 0.40 -> 125 shares.
    assert shares(Decimal("50"), Decimal("0.40")) == Decimal("125")


def test_shares_full_price():
    # ask = 1 -> one share per dollar.
    assert shares(Decimal("50"), Decimal("1")) == Decimal("50")


def test_shares_rejects_zero_ask():
    with pytest.raises(ValueError):
        shares(Decimal("50"), Decimal("0"))


def test_shares_rejects_ask_above_one():
    with pytest.raises(ValueError):
        shares(Decimal("50"), Decimal("1.01"))


# --- profit_if_win: stake * (1 - ask) / ask -----------------------------------


def test_profit_if_win_worked_example():
    # 125 shares cost $50, pay out $125 -> profit $75. 50 * (1 - 0.40) / 0.40 = 75.
    assert profit_if_win(Decimal("50"), Decimal("0.40")) == Decimal("75")


def test_profit_if_win_half_price():
    # 50 * (1 - 0.50) / 0.50 = 50.
    assert profit_if_win(Decimal("50"), Decimal("0.50")) == Decimal("50")


def test_profit_if_win_full_price_is_zero():
    # ask = 1 -> no profit possible.
    assert profit_if_win(Decimal("50"), Decimal("1")) == Decimal("0")


# --- profit_if_loss: -stake ---------------------------------------------------


def test_profit_if_loss_is_negative_stake():
    assert profit_if_loss(Decimal("50")) == Decimal("-50")


# --- expected_value: p * profit_if_win - (1 - p) * stake ----------------------


def test_expected_value_worked_example():
    # p=0.55, stake=50, ask=0.40: 0.55*75 - 0.45*50 = 41.25 - 22.50 = 18.75.
    ev = expected_value(Decimal("50"), Decimal("0.40"), Decimal("0.55"))
    assert ev == Decimal("18.75")


def test_expected_value_fair_bet_is_zero():
    # p == ask: a fair price has zero EV. p=0.40, ask=0.40, stake=50:
    # 0.40 * 50*(0.6/0.4) - 0.60 * 50 = 0.40*75 - 30 = 30 - 30 = 0.
    ev = expected_value(Decimal("50"), Decimal("0.40"), Decimal("0.40"))
    assert ev == Decimal("0")


def test_expected_value_negative_when_overpaying():
    # p below the ask -> negative EV. p=0.30, ask=0.40, stake=50:
    # 0.30*75 - 0.70*50 = 22.5 - 35 = -12.5.
    ev = expected_value(Decimal("50"), Decimal("0.40"), Decimal("0.30"))
    assert ev == Decimal("-12.5")


def test_expected_value_rejects_bad_prob():
    with pytest.raises(ValueError):
        expected_value(Decimal("50"), Decimal("0.40"), Decimal("1.5"))


# --- prob_of_loss: 1 - p ------------------------------------------------------


def test_prob_of_loss_worked_example():
    assert prob_of_loss(Decimal("0.55")) == Decimal("0.45")


def test_prob_of_loss_certain_win():
    assert prob_of_loss(Decimal("1")) == Decimal("0")


# --- arb_locked_profit: net_edge * set_size -----------------------------------


def test_arb_locked_profit_worked_example():
    # 3c net edge on a $200 set -> $6 locked.
    assert arb_locked_profit(Decimal("0.03"), Decimal("200")) == Decimal("6.00")


# --- mark_to_market: shares * current_mid - stake -----------------------------


def test_mark_to_market_gain():
    # 125 shares now worth 0.60 each = $75, paid $50 -> +$25 unrealized.
    assert mark_to_market(Decimal("125"), Decimal("0.60"), Decimal("50")) == Decimal("25.00")


def test_mark_to_market_loss():
    # 125 shares now worth 0.30 = $37.50, paid $50 -> -$12.50.
    assert mark_to_market(Decimal("125"), Decimal("0.30"), Decimal("50")) == Decimal("-12.50")


# --- cross-check against the backtest payoff oracle ---------------------------


def _directional_candidate(side: str) -> BetCandidate:
    # An all-in fill of 0.40 = m_side 0.37 + half_spread 0.01 + slippage 0.01 + gas 0.01.
    return BetCandidate(
        entry_time=datetime(2026, 1, 1),
        resolve_time=datetime(2026, 2, 1),
        market_id="m1",
        condition_id="c1",
        strategy="extreme_correction",
        kind="directional",
        tag="crypto",
        side=side,
        p_yes=Decimal("0.55"),
        p_side=Decimal("0.55"),
        p_lo_side=Decimal("0.50"),
        m_side=Decimal("0.37"),
        half_spread=Decimal("0.01"),
        slippage=Decimal("0.01"),
        gas=Decimal("0.01"),
    )


def test_profit_if_win_matches_realized_pnl():
    # The dollar profit on a win must equal the backtest's realized P&L for a winning bet.
    c = _directional_candidate("yes")
    stake = Decimal("50")
    ask = c.m_side + c.half_spread + c.slippage + c.gas  # 0.40
    assert profit_if_win(stake, ask) == realized_pnl(c, stake, outcome=1)


def test_profit_if_loss_matches_realized_pnl():
    c = _directional_candidate("yes")
    stake = Decimal("50")
    assert profit_if_loss(stake) == realized_pnl(c, stake, outcome=0)
