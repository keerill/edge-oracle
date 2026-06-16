"""Raw CLOB WebSocket schema tests — the untrusted-boundary parse + the Decimal->string
wire contract for the published signal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from app.models.advisor import AdvisedSignal
from app.polymarket.schemas import RawWsBook, RawWsPriceChange, parse_ws_message


def test_parse_book_frame():
    msg = parse_ws_message(
        {
            "event_type": "book",
            "asset_id": "tok",
            "market": "0xcond",
            "bids": [{"price": "0.46", "size": "100"}],
            "asks": [{"price": "0.48", "size": "200"}],
        }
    )
    assert isinstance(msg, RawWsBook)
    assert msg.asset_id == "tok"
    assert msg.bids[0].price == "0.46"  # stays a string at the boundary


def test_parse_price_change_frame():
    msg = parse_ws_message(
        {
            "event_type": "price_change",
            "asset_id": "tok",
            "changes": [{"price": "0.47", "size": "0", "side": "SELL"}],
        }
    )
    assert isinstance(msg, RawWsPriceChange)
    assert msg.changes[0].side == "SELL"
    assert msg.changes[0].size == "0"


def test_unknown_event_types_are_ignored():
    assert parse_ws_message({"event_type": "tick_size_change", "asset_id": "tok"}) is None
    assert parse_ws_message({"event_type": "last_trade_price"}) is None
    assert parse_ws_message({}) is None  # no event_type


def test_extra_fields_are_tolerated():
    msg = parse_ws_message(
        {"event_type": "book", "asset_id": "tok", "hash": "abc", "new_field": 1}
    )
    assert isinstance(msg, RawWsBook)


def test_published_signal_serializes_decimals_as_strings():
    # The SSE payload must be byte-for-byte a /signals row: Decimal -> JSON string (no float).
    advised = AdvisedSignal(
        id="set_arb:m1",
        time=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
        market_id="m1",
        condition_id="c1",
        market_question="Q?",
        strategy="set_arb",
        kind="long_set",
        market_price=Decimal("0.95"),
        p=None,
        edge=Decimal("0.05"),
        net_edge=Decimal("0.03"),
        recommended_size_usd=Decimal(0),
        recommended_size_pct=Decimal(0),
        confidence=Decimal(1),
        gate_passed=True,
        gate=None,
    )
    payload = json.loads(advised.model_dump_json())
    assert payload["net_edge"] == "0.03"  # string, not 0.03 float
    assert payload["market_price"] == "0.95"
    assert payload["id"] == "set_arb:m1"
