"""Price-extreme correction tests — sync, offline, deterministic.

Boundary-driven: the named edges (0.04, 0.10, 0.80, 0.96), the open-band edges (0.15/0.85),
the most-extreme clamp (0.0/1.0), and out-of-range guards. ``fair_value`` is exact
``Decimal`` (quantized where the nudge division doesn't terminate). Never float.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.math.correction import CorrectionParams, evaluate_extreme_correction

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
P = CorrectionParams()
Q6 = Decimal("0.000001")


def _eval(m: str):
    return evaluate_extreme_correction(Decimal(m), P, market_id="m1", condition_id="c1", at=AT)


# --- the four named boundaries ------------------------------------------------


def test_boundary_004_corrects_up():
    sig = _eval("0.04")
    assert sig is not None
    assert sig.strategy == "extreme_correction"
    assert sig.kind == "correction"
    assert sig.price == Decimal("0.04")
    # d=0.46; frac=(0.46-0.35)/0.15=0.7333..; nudge=0.03+0.7333*0.05=0.066667;
    # corrected = 0.04 + 0.066667 = 0.106667.
    assert sig.fair_value.quantize(Q6) == Decimal("0.106667")
    assert isinstance(sig.fair_value, Decimal)
    assert sig.time == AT


def test_boundary_010_corrects_up():
    sig = _eval("0.10")
    assert sig is not None
    # d=0.40; frac=(0.40-0.35)/0.15=0.3333..; nudge=0.046667; corrected=0.146667.
    assert sig.fair_value.quantize(Q6) == Decimal("0.146667")


def test_boundary_080_is_not_extreme():
    # 0.15 <= 0.80 <= 0.85 -> not extreme -> no correction.
    assert _eval("0.80") is None


def test_boundary_096_corrects_down():
    sig = _eval("0.96")
    assert sig is not None
    assert sig.price == Decimal("0.96")
    # symmetric to 0.04: nudge 0.066667; corrected = 0.96 - 0.066667 = 0.893333.
    assert sig.fair_value.quantize(Q6) == Decimal("0.893333")


# --- open-band edges (lo/hi themselves are NOT corrected) --------------------


def test_low_band_edge_is_not_corrected():
    assert _eval("0.15") is None  # 0.15 is not < 0.15


def test_high_band_edge_is_not_corrected():
    assert _eval("0.85") is None  # 0.85 is not > 0.85


def test_just_inside_low_band_corrects_up():
    sig = _eval("0.149")
    assert sig is not None and sig.fair_value > Decimal("0.149")


def test_just_inside_high_band_corrects_down():
    sig = _eval("0.851")
    assert sig is not None and sig.fair_value < Decimal("0.851")


def test_midpoint_is_not_corrected():
    assert _eval("0.50") is None


# --- most-extreme clamp -------------------------------------------------------


def test_extreme_zero_clamps_to_max_nudge():
    sig = _eval("0.0")
    assert sig is not None
    # d=0.50 -> frac clamps to 1 -> nudge = nudge_max = 0.08; corrected = 0.08.
    assert sig.fair_value == Decimal("0.08")


def test_extreme_one_clamps_to_max_nudge():
    sig = _eval("1.0")
    assert sig is not None
    assert sig.fair_value == Decimal("0.92")  # 1.0 - 0.08


def test_correction_never_overshoots_target():
    # Even the most extreme correction stays well short of 0.50.
    assert _eval("0.0").fair_value < Decimal("0.50")
    assert _eval("1.0").fair_value > Decimal("0.50")


# --- input validation ---------------------------------------------------------


def test_negative_price_raises():
    with pytest.raises(ValueError):
        _eval("-0.01")


def test_price_above_one_raises():
    with pytest.raises(ValueError):
        _eval("1.5")
