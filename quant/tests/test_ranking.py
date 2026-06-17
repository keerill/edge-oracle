"""Safety-ranking tests — the "only the safest first" order and filters. Pure, offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.advisor.ranking import rank_signals, safety_rank_key, safety_tier
from app.models.advisor import AdvisedSignal

T = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _advised(
    strategy: str,
    net_edge: str,
    *,
    gate_passed: bool,
    market_id: str = "m1",
) -> AdvisedSignal:
    return AdvisedSignal(
        id=f"{strategy}:{market_id}:1",
        time=T,
        market_id=market_id,
        condition_id="c1",
        strategy=strategy,  # type: ignore[arg-type]
        kind="x",
        market_price=Decimal("0.5"),
        p=None,
        edge=Decimal("0"),
        net_edge=Decimal(net_edge),
        recommended_size_usd=Decimal("0"),
        recommended_size_pct=Decimal("0"),
        confidence=Decimal("0"),
        gate_passed=gate_passed,
        gate=None,
    )


def test_safety_tier_buckets():
    assert safety_tier(_advised("set_arb", "0.01", gate_passed=True)) == 0
    assert safety_tier(_advised("extreme_correction", "0.05", gate_passed=True)) == 1
    assert safety_tier(_advised("extreme_correction", "-0.01", gate_passed=False)) == 2
    assert safety_tier(_advised("favourite_longshot", "0", gate_passed=False)) == 2


def test_safety_rank_key_orders_tier_then_net_edge():
    a = _advised("extreme_correction", "0.02", gate_passed=True)
    b = _advised("extreme_correction", "0.06", gate_passed=True)
    # same tier -> bigger net_edge ranks first (smaller key)
    assert safety_rank_key(b) < safety_rank_key(a)


def test_safety_sort_puts_arb_first_then_gated_directional():
    arb = _advised("set_arb", "0.01", gate_passed=True, market_id="arb")
    dir_big = _advised("extreme_correction", "0.06", gate_passed=True, market_id="d1")
    dir_small = _advised("extreme_correction", "0.02", gate_passed=True, market_id="d2")
    gated_out = _advised("extreme_correction", "-0.03", gate_passed=False, market_id="d3")
    longshot = _advised("favourite_longshot", "0", gate_passed=False, market_id="ls")

    ranked = rank_signals([dir_small, longshot, arb, gated_out, dir_big], sort="safety")
    # tier 0 arb; tier 1 directional by net_edge desc (d1 0.06 > d2 0.02); tier 2 by net_edge
    # desc too (ls 0 > d3 -0.03).
    assert [a.market_id for a in ranked] == ["arb", "d1", "d2", "ls", "d3"]


def test_net_edge_sort_is_default_and_ignores_tiers():
    arb = _advised("set_arb", "0.01", gate_passed=True, market_id="arb")
    dir_big = _advised("extreme_correction", "0.06", gate_passed=True, market_id="d1")
    ranked = rank_signals([arb, dir_big])  # default net_edge desc
    assert [a.market_id for a in ranked] == ["d1", "arb"]


def test_safe_only_drops_tier_2():
    arb = _advised("set_arb", "0.01", gate_passed=True, market_id="arb")
    gated_out = _advised("extreme_correction", "-0.03", gate_passed=False, market_id="d3")
    longshot = _advised("favourite_longshot", "0", gate_passed=False, market_id="ls")
    ranked = rank_signals([arb, gated_out, longshot], sort="safety", safe_only=True)
    assert [a.market_id for a in ranked] == ["arb"]


def test_min_net_edge_filters():
    a = _advised("extreme_correction", "0.02", gate_passed=True, market_id="d1")
    b = _advised("extreme_correction", "0.06", gate_passed=True, market_id="d2")
    ranked = rank_signals([a, b], min_net_edge=Decimal("0.05"))
    assert [x.market_id for x in ranked] == ["d2"]
