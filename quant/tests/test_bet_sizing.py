"""Bet-sizing math tests — the money-correctness core. Sync, offline, deterministic.

These tests ARE the spec for Kelly sizing: hand-computed ``Decimal`` worked examples,
exact ``==`` (quantized only where the division doesn't terminate), and the gate / cap
boundaries. All results must be exact ``Decimal`` (never float). Covers the raw Kelly
fraction, the fractional + hard-capped fraction, the edge gate, the bankroll->stake
pipeline, and the per-tag correlation cap.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.math.bet_sizing import (
    TaggedStake,
    cap_correlated_stakes,
    edge_gate,
    fractional_kelly,
    kelly_fraction,
    position_size,
)

Q2 = Decimal("0.01")


# --- kelly_fraction: f* = (p - m) / (1 - m) ----------------------------------


def test_kelly_fraction_worked_example():
    # (0.55 - 0.40) / (1 - 0.40) = 0.15 / 0.60 = 0.25 exactly.
    f = kelly_fraction(Decimal("0.55"), Decimal("0.40"))
    assert f == Decimal("0.25")
    assert isinstance(f, Decimal)


def test_kelly_fraction_second_example():
    # (0.60 - 0.50) / (1 - 0.50) = 0.10 / 0.50 = 0.20.
    assert kelly_fraction(Decimal("0.60"), Decimal("0.50")) == Decimal("0.20")


def test_kelly_fraction_zero_price_is_full_edge():
    # m = 0: (0.55 - 0) / (1 - 0) = 0.55.
    assert kelly_fraction(Decimal("0.55"), Decimal("0")) == Decimal("0.55")


def test_kelly_fraction_no_edge_when_p_equals_m():
    # p == m -> no edge -> exactly 0 (not a division artifact).
    assert kelly_fraction(Decimal("0.40"), Decimal("0.40")) == Decimal("0")


def test_kelly_fraction_no_edge_when_p_below_m():
    # p < m -> take the other side -> 0.
    assert kelly_fraction(Decimal("0.30"), Decimal("0.40")) == Decimal("0")


@pytest.mark.parametrize(
    ("p", "m"),
    [
        ("1.5", "0.40"),    # p > 1
        ("-0.01", "0.40"),  # p < 0
        ("0.55", "-0.01"),  # m < 0
        ("0.99", "1.0"),    # m == 1 -> denominator 0, no defined fraction
        ("0.55", "1.5"),    # m > 1
    ],
)
def test_kelly_fraction_out_of_range_raises(p, m):
    with pytest.raises(ValueError):
        kelly_fraction(Decimal(p), Decimal(m))


# --- fractional_kelly: max(0, min(frac * kelly, cap)) ------------------------


def test_fractional_kelly_caps_the_worked_example():
    # kelly 0.25; quarter-Kelly 0.25 * 0.25 = 0.0625; hard cap 0.05 binds.
    f = fractional_kelly(Decimal("0.55"), Decimal("0.40"))
    assert f == Decimal("0.05")
    assert isinstance(f, Decimal)


def test_fractional_kelly_uncapped_path():
    # kelly (0.52 - 0.50) / 0.50 = 0.04; quarter 0.01 < cap 0.05 -> 0.01.
    assert fractional_kelly(Decimal("0.52"), Decimal("0.50")) == Decimal("0.01")


def test_fractional_kelly_high_cap_leaves_quarter_kelly():
    # cap raised to 1 -> the 0.0625 quarter-Kelly is returned uncapped.
    assert fractional_kelly(Decimal("0.55"), Decimal("0.40"), cap=Decimal("1")) == Decimal("0.0625")


def test_fractional_kelly_custom_frac_still_capped():
    # half-Kelly 0.5 * 0.25 = 0.125; cap 0.05 still binds.
    assert fractional_kelly(Decimal("0.55"), Decimal("0.40"), frac=Decimal("0.5")) == Decimal("0.05")


def test_fractional_kelly_no_edge_is_zero():
    # p < m -> kelly 0 -> floored at 0.
    assert fractional_kelly(Decimal("0.30"), Decimal("0.40")) == Decimal("0")


@pytest.mark.parametrize(("frac", "cap"), [("-0.01", "0.05"), ("0.25", "-0.01")])
def test_fractional_kelly_negative_knobs_raise(frac, cap):
    with pytest.raises(ValueError):
        fractional_kelly(Decimal("0.55"), Decimal("0.40"), frac=Decimal(frac), cap=Decimal(cap))


# --- edge_gate: p_lo > m + half_spread + slippage + gas ----------------------


def test_edge_gate_rejects_1c_edge_against_2c_half_spread():
    # The spec's headline gate: a 1c edge (p_lo 0.41 vs m 0.40) cannot clear a 2c
    # half-spread -> rejected, even before slippage/gas.
    assert edge_gate(Decimal("0.41"), Decimal("0.40"), Decimal("0.02"), Decimal("0"), Decimal("0")) is False


def test_edge_gate_accepts_when_edge_clears_all_costs():
    # threshold 0.40 + 0.02 + 0.01 + 0.01 = 0.44; p_lo 0.50 > 0.44.
    assert edge_gate(Decimal("0.50"), Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01")) is True


def test_edge_gate_rejects_exact_break_even():
    # p_lo exactly equals the threshold 0.44 -> strict ">" rejects.
    assert edge_gate(Decimal("0.44"), Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01")) is False


def test_edge_gate_accepts_just_over_threshold():
    assert edge_gate(Decimal("0.4401"), Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01")) is True


def test_edge_gate_costless_1c_edge_passes():
    # With no spread/slippage/gas a 1c edge clears: 0.41 > 0.40.
    assert edge_gate(Decimal("0.41"), Decimal("0.40"), Decimal("0"), Decimal("0"), Decimal("0")) is True


# --- position_size: gate, then fractional Kelly on the ask, times bankroll ----


def test_position_size_gated_out_is_zero():
    # p would size (0.55) but p_lo 0.41 fails the 0.44 gate -> no bet.
    stake = position_size(
        Decimal("10000"), Decimal("0.55"), Decimal("0.41"),
        Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
    )
    assert stake == Decimal("0")


def test_position_size_cap_binds():
    # gate 0.50 > 0.44 passes. ask = 0.40 + 0.02 = 0.42. kelly on p=0.55:
    #   (0.55 - 0.42) / (1 - 0.42) = 0.13 / 0.58 = 0.2241..; quarter 0.0560.. -> capped 0.05.
    #   stake = 10000 * 0.05 = 500. (The 500 confirms p, not p_lo, sizes the bet.)
    stake = position_size(
        Decimal("10000"), Decimal("0.55"), Decimal("0.50"),
        Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
    )
    assert stake == Decimal("500.00")
    assert isinstance(stake, Decimal)


def test_position_size_uncapped_kelly_path():
    # ask = 0.45 + 0.02 = 0.47. kelly (0.55 - 0.47) / (1 - 0.47) = 0.08 / 0.53 = 0.1509..;
    #   quarter 0.03774.. < cap -> stake = 10000 * 0.03774.. = 377.358.. -> 377.36.
    stake = position_size(
        Decimal("10000"), Decimal("0.55"), Decimal("0.50"),
        Decimal("0.45"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
    )
    assert stake.quantize(Q2) == Decimal("377.36")


def test_position_size_custom_frac_and_cap():
    # ask 0.42; kelly 0.2241..; half-Kelly 0.1120.. under cap 1 -> 10000 * 0.1120.. = 1120.69.
    stake = position_size(
        Decimal("10000"), Decimal("0.55"), Decimal("0.50"),
        Decimal("0.40"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
        frac=Decimal("0.5"), cap=Decimal("1"),
    )
    assert stake.quantize(Q2) == Decimal("1120.69")


def test_position_size_zero_bankroll_is_zero():
    # Edge clears the gate but there's nothing to stake.
    stake = position_size(
        Decimal("0"), Decimal("0.55"), Decimal("0.50"),
        Decimal("0.45"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
    )
    assert stake == Decimal("0")


def test_position_size_negative_bankroll_raises():
    with pytest.raises(ValueError):
        position_size(
            Decimal("-1"), Decimal("0.55"), Decimal("0.50"),
            Decimal("0.45"), Decimal("0.02"), Decimal("0.01"), Decimal("0.01"),
        )


# --- cap_correlated_stakes: one macro theme = one capped bet ------------------


def test_correlation_guard_pro_rata_scales_a_tag_group():
    # fed total 600 > cap 500 -> scale each by 500/600; cpi (100) under cap untouched.
    #   300 * 500 / 600 = 250 exactly (multiply-first keeps the ratio exact).
    out = cap_correlated_stakes(
        [
            TaggedStake("fed", Decimal("300")),
            TaggedStake("fed", Decimal("300")),
            TaggedStake("cpi", Decimal("100")),
        ],
        Decimal("500"),
    )
    assert [t.stake for t in out] == [Decimal("250"), Decimal("250"), Decimal("100")]
    assert [t.tag for t in out] == ["fed", "fed", "cpi"]
    assert all(isinstance(t.stake, Decimal) for t in out)


def test_correlation_guard_uneven_split_quantized():
    # fed total 600 > 500. 400*500/600 = 333.33..; 200*500/600 = 166.66..; sum 500.
    out = cap_correlated_stakes(
        [TaggedStake("fed", Decimal("400")), TaggedStake("fed", Decimal("200"))],
        Decimal("500"),
    )
    assert out[0].stake.quantize(Q2) == Decimal("333.33")
    assert out[1].stake.quantize(Q2) == Decimal("166.67")


def test_correlation_guard_under_cap_is_unchanged():
    positions = [TaggedStake("a", Decimal("100")), TaggedStake("b", Decimal("200"))]
    assert cap_correlated_stakes(positions, Decimal("500")) == positions


def test_correlation_guard_single_position_capped():
    # 800 * 500 / 800 = 500 exactly.
    out = cap_correlated_stakes([TaggedStake("x", Decimal("800"))], Decimal("500"))
    assert out == [TaggedStake("x", Decimal("500"))]


def test_correlation_guard_preserves_order_with_interleaved_tags():
    # Same tag at non-adjacent indices still shares one cap; order is preserved.
    out = cap_correlated_stakes(
        [
            TaggedStake("fed", Decimal("300")),
            TaggedStake("cpi", Decimal("100")),
            TaggedStake("fed", Decimal("300")),
        ],
        Decimal("500"),
    )
    assert out == [
        TaggedStake("fed", Decimal("250")),
        TaggedStake("cpi", Decimal("100")),
        TaggedStake("fed", Decimal("250")),
    ]


def test_correlation_guard_empty_is_empty():
    assert cap_correlated_stakes([], Decimal("500")) == []


def test_correlation_guard_negative_cap_raises():
    with pytest.raises(ValueError):
        cap_correlated_stakes([TaggedStake("a", Decimal("100"))], Decimal("-1"))


def test_correlation_guard_negative_stake_raises():
    with pytest.raises(ValueError):
        cap_correlated_stakes([TaggedStake("a", Decimal("-5"))], Decimal("500"))
