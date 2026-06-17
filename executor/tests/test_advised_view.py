"""Contract test: the executor's read-side ``AdvisedSignalView`` parses a REAL advisor payload.

The golden fixture is a verbatim ``AdvisedSignal.model_dump_json()`` from ``quant`` (money as
JSON strings, ``time`` as ISO-8601). This is the cross-layer contract — the executor consumes
the advisor over Redis WITHOUT importing ``quant``; if the advisor's shape drifts, this fails.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.models.advised import AdvisedSignalView

FIXTURE = Path(__file__).parent / "fixtures" / "advised_signal.json"


def test_parses_real_advisor_payload_with_exact_decimals():
    view = AdvisedSignalView.model_validate_json(FIXTURE.read_text())
    assert view.strategy == "extreme_correction"
    assert view.kind == "buy_no"
    # money arrives as JSON strings -> exact Decimal (no float in the money path)
    assert view.market_price == Decimal("0.20")
    assert view.recommended_size_usd == Decimal("100")
    assert view.gate is not None
    assert view.gate.half_spread == Decimal("0.05")
    assert view.gate.threshold == Decimal("0.27")


def test_extra_fields_are_tolerated_for_additive_drift():
    payload = json.loads(FIXTURE.read_text())
    payload["some_new_field_added_later"] = 123
    view = AdvisedSignalView.model_validate(payload)
    assert view.market_id == "m1"
