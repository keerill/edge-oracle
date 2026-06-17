"""Pure resolution helpers — realized-outcome detection + journaling a prediction."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.ingestion.resolution import calibration_from_resolution, resolved_outcome
from app.models.signal import ExtremeCorrectionSignal

AT = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_yes_won_is_outcome_1():
    assert resolved_outcome(["Yes", "No"], ["1", "0"]) == 1


def test_no_won_is_outcome_0():
    assert resolved_outcome(["Yes", "No"], ["0", "1"]) == 0


def test_yes_index_respected_when_order_flipped():
    # if the API ever lists No first, the YES outcome still maps correctly
    assert resolved_outcome(["No", "Yes"], ["0", "1"]) == 1


def test_pending_or_tie_is_none():
    assert resolved_outcome(["Yes", "No"], ["0.5", "0.5"]) is None
    assert resolved_outcome(["Yes", "No"], []) is None


def test_non_binary_market_is_none():
    assert resolved_outcome(["Over", "Under"], ["1", "0"]) is None
    assert resolved_outcome(["Yes", "No", "Maybe"], ["1", "0", "0"]) is None


def test_calibration_record_from_resolution():
    sig = ExtremeCorrectionSignal(
        time=AT, market_id="m1", condition_id="c1",
        kind="correction", price=Decimal("0.10"), fair_value=Decimal("0.147"),
    )
    rec = calibration_from_resolution(sig, outcome=1, at=AT)
    assert rec.strategy == "extreme_correction"
    assert rec.estimate == Decimal("0.147")
    assert rec.price == Decimal("0.10")
    assert rec.outcome == 1
    assert rec.market_id == "m1"
