"""Worked-number unit tests for the advisor enrichment (``app.advisor.view``).

Pure, offline, exact ``Decimal`` — these are the spec for the live sizing the REST layer
serves. The directional numbers reuse the proven ``position_size`` anchors from
``test_bet_sizing.py`` so the advisor and the sizing math stay in lock-step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.advisor.view import _confidence, advise
from app.models.quote import QuoteSnapshot
from app.models.signal import ArbSignal, ExtremeCorrectionSignal, FavouriteLongshotSignal

T = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _quote(token_id: str, mid: str, spread: str) -> QuoteSnapshot:
    """A top-of-book snapshot carrying just the midpoint/spread the sizer reads."""
    half = Decimal(spread) / 2
    return QuoteSnapshot(
        time=T,
        token_id=token_id,
        market_id="m1",
        best_bid=Decimal(mid) - half,
        best_bid_size=Decimal("100"),
        best_ask=Decimal(mid) + half,
        best_ask_size=Decimal("100"),
        midpoint=Decimal(mid),
        spread=Decimal(spread),
    )


def _correction(price: str, fair_value: str) -> ExtremeCorrectionSignal:
    return ExtremeCorrectionSignal(
        time=T, market_id="m1", condition_id="c1", price=Decimal(price), fair_value=Decimal(fair_value)
    )


def test_directional_buy_yes_cap_binds():
    # price 0.40 < fair_value 0.55 -> buy YES, p_side = 0.55. YES quote mid 0.40 / spread 0.04.
    sig = _correction("0.40", "0.55")
    yes_q = _quote("111", "0.40", "0.04")
    a = advise(
        sig,
        signal_id="extreme_correction:m1:1",
        yes_quote=yes_q,
        no_quote=_quote("222", "0.60", "0.04"),
        bankroll=Decimal("10000"),
    )
    assert a.strategy == "extreme_correction"
    assert a.kind == "buy_yes"
    assert a.p == Decimal("0.55")
    assert a.market_price == Decimal("0.40")
    # threshold = 0.40 + 0.02 + 0.01 + 0.01 = 0.44; p_lo = 0.55 - 0.05 = 0.50 -> gate passes.
    assert a.gate_passed is True
    assert a.gate is not None
    assert a.gate.threshold == Decimal("0.44")
    assert a.gate.p_lo == Decimal("0.50")
    assert a.gate.half_spread == Decimal("0.02")
    # ask = 0.42; kelly = 0.13/0.58 = 0.2241..., quarter = 0.0560... -> CAPPED at 0.05.
    assert a.recommended_size_usd == Decimal("500.00")  # 10000 * 0.05
    assert a.recommended_size_pct == Decimal("0.05")
    assert a.edge == Decimal("0.13")  # p_side - ask = 0.55 - 0.42
    assert a.net_edge == Decimal("0.06")  # p_lo - threshold = 0.50 - 0.44
    # confidence = (0.50 - 0.44) / (1 - 0.44) = 0.06 / 0.56
    assert a.confidence.quantize(Decimal("0.000001")) == Decimal("0.107143")


def test_directional_gated_out():
    # Same market but fair_value 0.46 -> p_side 0.46, p_lo 0.41 <= threshold 0.44 -> no bet.
    sig = _correction("0.40", "0.46")
    a = advise(
        sig,
        signal_id="extreme_correction:m1:2",
        yes_quote=_quote("111", "0.40", "0.04"),
        no_quote=_quote("222", "0.60", "0.04"),
        bankroll=Decimal("10000"),
    )
    assert a.gate_passed is False
    assert a.recommended_size_usd == Decimal("0")
    assert a.recommended_size_pct == Decimal("0")
    assert a.net_edge == Decimal("-0.03")  # 0.41 - 0.44, sorts last
    assert a.edge == Decimal("0.04")  # 0.46 - 0.42
    assert a.confidence == Decimal("0")


def test_directional_buy_no_uses_no_token_quote():
    # price 0.80 > fair_value 0.30 -> buy NO, p_side = 1 - 0.30 = 0.70. MUST size off the NO
    # token's quote (mid 0.18 / spread 0.04), NOT the YES quote — proves the side mapping.
    sig = _correction("0.80", "0.30")
    a = advise(
        sig,
        signal_id="extreme_correction:m1:3",
        yes_quote=_quote("111", "0.80", "0.04"),
        no_quote=_quote("222", "0.18", "0.04"),
        bankroll=Decimal("10000"),
    )
    assert a.kind == "buy_no"
    assert a.p == Decimal("0.70")
    assert a.market_price == Decimal("0.18")  # the NO midpoint, not 0.80
    assert a.gate is not None
    assert a.gate.threshold == Decimal("0.22")  # 0.18 + 0.02 + 0.01 + 0.01
    assert a.gate.p_lo == Decimal("0.65")  # 0.70 - 0.05
    # ask = 0.20; kelly = (0.70-0.20)/0.80 = 0.625, quarter = 0.15625 -> CAPPED 0.05.
    assert a.recommended_size_usd == Decimal("500.00")
    assert a.gate_passed is True


def test_directional_degrades_without_side_quote():
    # The NO token has no quote -> can't price the ask -> gated, zero-size (defensive path).
    sig = _correction("0.80", "0.30")  # buy_no
    a = advise(
        sig,
        signal_id="extreme_correction:m1:4",
        yes_quote=_quote("111", "0.80", "0.04"),
        no_quote=None,
        bankroll=Decimal("10000"),
    )
    assert a.kind == "buy_no"
    assert a.gate is None
    assert a.gate_passed is False
    assert a.recommended_size_usd == Decimal("0")
    assert a.confidence == Decimal("0")
    assert a.market_price == Decimal("0.80")  # falls back to the signal's own price


def test_arb_is_risk_free_and_unsized():
    sig = ArbSignal(
        time=T,
        market_id="m1",
        condition_id="c1",
        kind="long_set",
        yes_price=Decimal("0.46"),
        no_price=Decimal("0.49"),
        set_size=Decimal("1"),
        gross_edge=Decimal("0.05"),
        estimated_costs=Decimal("0.02"),
        net_edge=Decimal("0.03"),
        hypothetical_pnl=Decimal("0.03"),
    )
    a = advise(sig, signal_id="set_arb:m1:5", bankroll=Decimal("10000"))
    assert a.strategy == "set_arb"
    assert a.kind == "long_set"
    assert a.p is None
    assert a.gate is None
    assert a.gate_passed is True
    assert a.confidence == Decimal("1")
    assert a.edge == Decimal("0.05")
    assert a.net_edge == Decimal("0.03")
    assert a.market_price == Decimal("0.95")  # set cost = 0.46 + 0.49
    assert a.recommended_size_usd == Decimal("0")


def test_longshot_is_display_only():
    sig = FavouriteLongshotSignal(
        time=T, market_id="m1", condition_id="c1", kind="buy_no", price=Decimal("0.10"), edge_score=Decimal("0.5")
    )
    a = advise(sig, signal_id="favourite_longshot:m1:6", bankroll=Decimal("10000"))
    assert a.strategy == "favourite_longshot"
    assert a.kind == "buy_no"
    assert a.p is None
    assert a.gate is None
    assert a.gate_passed is False
    assert a.confidence == Decimal("0.5")  # the heuristic strength lives here, not in net_edge
    assert a.edge == Decimal("0")  # no money edge — never out-ranks an actionable signal
    assert a.net_edge == Decimal("0")
    assert a.market_price == Decimal("0.10")
    assert a.recommended_size_usd == Decimal("0")


def test_confidence_boundaries():
    assert _confidence(Decimal("0.44"), Decimal("0.44")) == Decimal("0")  # break-even -> 0
    assert _confidence(Decimal("0.30"), Decimal("0.44")) == Decimal("0")  # below -> clamps to 0
    assert _confidence(Decimal("0.50"), Decimal("1.0")) == Decimal("0")  # no room above cost -> 0
    assert _confidence(Decimal("1.0"), Decimal("0.50")) == Decimal("1")  # certainty -> clamps to 1
