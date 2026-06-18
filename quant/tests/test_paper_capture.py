"""Offline unit tests for the pure paper-capture logic (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.advisor import AdvisedSignal, Economics, GateBreakdown
from app.paper.capture import paper_trade_from_advice, select_new_paper_trades

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _directional(
    *,
    market_id="m1",
    condition_id="c1",
    kind="buy_yes",
    gate_passed=True,
    size=Decimal("50"),
    ask=Decimal("0.42"),
    p=Decimal("0.55"),
    p_lo=Decimal("0.50"),
    net_edge=Decimal("0.06"),
    time=T0,
) -> AdvisedSignal:
    return AdvisedSignal(
        id=f"extreme_correction:{market_id}:{int(time.timestamp() * 1000)}",
        time=time,
        market_id=market_id,
        condition_id=condition_id,
        strategy="extreme_correction",
        kind=kind,
        market_price=ask,
        p=p,
        edge=Decimal("0.05"),
        net_edge=net_edge,
        recommended_size_usd=size,
        recommended_size_pct=Decimal("0.05"),
        confidence=Decimal("0.4"),
        gate_passed=gate_passed,
        gate=GateBreakdown(
            m=ask,
            half_spread=Decimal("0.01"),
            slippage=Decimal("0.01"),
            gas=Decimal("0.01"),
            margin=Decimal("0.05"),
            p_lo=p_lo,
            threshold=Decimal("0.44"),
        ),
        economics=Economics(ask=ask, stake_usd=size),
    )


def _arb(*, market_id="m2", condition_id="c2", net_edge=Decimal("0.03"), time=T0) -> AdvisedSignal:
    return AdvisedSignal(
        id=f"set_arb:{market_id}:{int(time.timestamp() * 1000)}",
        time=time,
        market_id=market_id,
        condition_id=condition_id,
        strategy="set_arb",
        kind="long_set",
        market_price=Decimal("0.96"),
        p=None,
        edge=Decimal("0.04"),
        net_edge=net_edge,
        recommended_size_usd=Decimal("0"),
        recommended_size_pct=Decimal("0"),
        confidence=Decimal("1"),
        gate_passed=True,
        gate=None,
        economics=Economics(locked_profit_usd=net_edge, prob_of_loss=Decimal("0")),
    )


def _longshot(*, market_id="m3", condition_id="c3") -> AdvisedSignal:
    return AdvisedSignal(
        id=f"favourite_longshot:{market_id}:0",
        time=T0,
        market_id=market_id,
        condition_id=condition_id,
        strategy="favourite_longshot",
        kind="favourite",
        market_price=Decimal("0.9"),
        p=None,
        edge=Decimal("0"),
        net_edge=Decimal("0"),
        recommended_size_usd=Decimal("0"),
        recommended_size_pct=Decimal("0"),
        confidence=Decimal("0.7"),
        gate_passed=False,
        gate=None,
    )


def test_directional_gated_captures_with_shares_from_ask() -> None:
    pt = paper_trade_from_advice(_directional(size=Decimal("50"), ask=Decimal("0.40")))
    assert pt is not None
    assert pt.side == "yes"
    assert pt.advised_price == Decimal("0.40")
    assert pt.stake_usd == Decimal("50")
    assert pt.shares == Decimal("125")  # 50 / 0.40
    assert pt.p == Decimal("0.55") and pt.p_lo == Decimal("0.50")
    assert pt.edge == Decimal("0.06")  # net_edge


def test_buy_no_maps_to_no_side() -> None:
    assert paper_trade_from_advice(_directional(kind="buy_no")).side == "no"


def test_directional_not_captured_when_gate_fails() -> None:
    assert paper_trade_from_advice(_directional(gate_passed=False)) is None


def test_directional_not_captured_when_zero_size() -> None:
    assert paper_trade_from_advice(_directional(size=Decimal("0"))) is None


def test_arb_captured_when_positive_edge() -> None:
    pt = paper_trade_from_advice(_arb(net_edge=Decimal("0.03")))
    assert pt is not None
    assert pt.side == "set"
    assert pt.stake_usd == Decimal("1") and pt.shares == Decimal("1")
    assert pt.p is None and pt.p_lo is None
    assert pt.edge == Decimal("0.03")


def test_arb_not_captured_when_nonpositive_edge() -> None:
    assert paper_trade_from_advice(_arb(net_edge=Decimal("0"))) is None


def test_longshot_never_captured() -> None:
    assert paper_trade_from_advice(_longshot()) is None


def test_select_dedups_against_already_open() -> None:
    advised = [_directional(market_id="m1", condition_id="c1")]
    out = select_new_paper_trades(advised, already_open={("extreme_correction", "c1")})
    assert out == []


def test_select_keeps_newest_per_strategy_market() -> None:
    older = _directional(condition_id="c1", time=datetime(2026, 6, 1, 11, 0, tzinfo=UTC))
    newer = _directional(condition_id="c1", time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
    out = select_new_paper_trades([older, newer], already_open=set())
    assert len(out) == 1
    assert out[0].advised_at == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def test_select_mixed_strategies_and_filters() -> None:
    advised = [_directional(), _arb(), _longshot(), _directional(gate_passed=False)]
    out = select_new_paper_trades(advised, already_open=set())
    keys = {(pt.strategy, pt.condition_id) for pt in out}
    assert keys == {("extreme_correction", "c1"), ("set_arb", "c2")}
