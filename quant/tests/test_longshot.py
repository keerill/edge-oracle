"""Favourite-longshot signal tests — sync, offline, deterministic.

Boundary-driven: the named edges (0.04, 0.10, 0.80, 0.96) plus the band edges, the dead
gaps, the true extremes, and out-of-range guards. ``edge_score`` is exact ``Decimal``
(quantized only where the division doesn't terminate). Never float.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.math.longshot import LongshotParams, evaluate_favourite_longshot

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
P = LongshotParams()
Q6 = Decimal("0.000001")


def _eval(m: str):
    return evaluate_favourite_longshot(Decimal(m), P, market_id="m1", condition_id="c1", at=AT)


# --- the four named boundaries ------------------------------------------------


def test_boundary_004_is_no_signal():
    # 0.04 is in the gap: above the true-extreme floor (0.03) but below the longshot
    # band (0.05) -> no signal.
    assert _eval("0.04") is None


def test_boundary_010_buys_no_at_band_midpoint():
    sig = _eval("0.10")
    assert sig is not None
    assert sig.kind == "buy_no"
    assert sig.strategy == "favourite_longshot"
    assert sig.price == Decimal("0.10")
    # (0.15 - 0.10) / (0.15 - 0.05) = 0.05 / 0.10 = 0.5 exactly.
    assert sig.edge_score == Decimal("0.5")
    assert isinstance(sig.edge_score, Decimal)
    assert sig.time == AT
    assert sig.market_id == "m1" and sig.condition_id == "c1"


def test_boundary_080_buys_yes():
    sig = _eval("0.80")
    assert sig is not None
    assert sig.kind == "buy_yes"
    assert sig.price == Decimal("0.80")
    # (0.80 - 0.75) / (0.92 - 0.75) = 0.05 / 0.17 = 0.2941176...
    assert sig.edge_score.quantize(Q6) == Decimal("0.294118")


def test_boundary_096_is_no_signal():
    # 0.96 is in the gap: above the favourite band (0.92), below the extreme ceiling (0.97).
    assert _eval("0.96") is None


# --- band edges (closed intervals); score is strongest toward the mispriced end ----------


def test_longshot_low_edge_is_strongest():
    sig = _eval("0.05")
    assert sig is not None and sig.kind == "buy_no"
    assert sig.edge_score == Decimal("1")  # (0.15 - 0.05) / 0.10


def test_longshot_high_edge_is_weakest():
    sig = _eval("0.15")
    assert sig is not None and sig.kind == "buy_no"
    assert sig.edge_score == Decimal("0")  # (0.15 - 0.15) / 0.10


def test_favourite_low_edge_is_weakest():
    sig = _eval("0.75")
    assert sig is not None and sig.kind == "buy_yes"
    assert sig.edge_score == Decimal("0")  # (0.75 - 0.75) / 0.17


def test_favourite_high_edge_is_strongest():
    sig = _eval("0.92")
    assert sig is not None and sig.kind == "buy_yes"
    assert sig.edge_score == Decimal("1")  # (0.92 - 0.75) / 0.17


# --- gaps and true extremes (all -> None) ------------------------------------


def test_just_below_longshot_band_is_none():
    assert _eval("0.045") is None


def test_just_above_longshot_band_is_none():
    assert _eval("0.151") is None


def test_efficient_middle_is_none():
    assert _eval("0.50") is None


def test_just_below_favourite_band_is_none():
    assert _eval("0.749") is None


def test_just_above_favourite_band_is_none():
    assert _eval("0.921") is None


def test_true_extreme_low_is_none():
    assert _eval("0.02") is None


def test_true_extreme_high_is_none():
    assert _eval("0.98") is None


# --- input validation ---------------------------------------------------------


def test_negative_price_raises():
    with pytest.raises(ValueError):
        _eval("-0.01")


def test_price_above_one_raises():
    with pytest.raises(ValueError):
        _eval("1.5")
