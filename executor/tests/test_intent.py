"""Intent model + canonical hashing + tamper detection (pure, no keys, no I/O).

The ``intent_hash`` is the security primitive: the signer recomputes it from the
fields and refuses to sign on a mismatch, so a tampered intent (altered recipient,
size, ...) between executor and signer is rejected. These tests pin that the hash is
deterministic, that any field change changes it, and that a sealed envelope verifies
while a doctored one does not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.intent import (
    Intent,
    IntentEnvelope,
    compute_intent_hash,
)

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
EXP = datetime(2026, 6, 17, 12, 5, 0, tzinfo=timezone.utc)


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1",
        created_at=T0,
        expiry=EXP,
        source_signal_id="extreme_correction:m1:1750161600000",
        action="clob_order",
        chain_id=137,
        market_id="m1",
        condition_id="c1",
        side="buy_no",
        size=Decimal("400"),
        max_price=Decimal("0.30"),
        max_slippage=Decimal("0.05"),
        notional_usd=Decimal("100"),
        to_address="0xExchange",
        token_id=None,
        approve_spender=None,
        approve_amount=None,
        nonce=7,
    )
    base.update(over)
    return Intent(**base)


def test_intent_hash_is_deterministic_for_equal_intents():
    a = _intent()
    b = _intent()
    assert compute_intent_hash(a) == compute_intent_hash(b)
    # sha256 hex
    assert len(compute_intent_hash(a)) == 64


def test_hash_changes_when_recipient_changes():
    assert compute_intent_hash(_intent()) != compute_intent_hash(_intent(to_address="0xAttacker"))


def test_hash_changes_when_size_changes():
    assert compute_intent_hash(_intent()) != compute_intent_hash(_intent(size=Decimal("400.0001")))


def test_hash_survives_json_roundtrip():
    # The real signer scenario: executor serializes the envelope to JSON, the signer parses
    # it back and recomputes the hash. The exact Decimal string survives the round trip, so
    # the recomputed hash matches — this is what makes the binding work across the boundary.
    env = IntentEnvelope.seal(_intent(max_price=Decimal("0.30"), size=Decimal("400")))
    reparsed = IntentEnvelope.model_validate_json(env.model_dump_json())
    assert reparsed.verify() is True
    assert compute_intent_hash(reparsed.intent) == env.intent_hash


def test_decimal_money_is_exact_not_float():
    # A genuinely different value hashes differently (no float rounding collapses neighbours).
    assert compute_intent_hash(_intent(max_price=Decimal("0.30"))) != compute_intent_hash(
        _intent(max_price=Decimal("0.300000001"))
    )


def test_envelope_seal_binds_the_hash_and_verifies():
    env = IntentEnvelope.seal(_intent())
    assert env.intent_hash == compute_intent_hash(env.intent)
    assert env.verify() is True


def test_envelope_with_tampered_hash_fails_verify():
    good = IntentEnvelope.seal(_intent())
    doctored = IntentEnvelope(
        intent=_intent(to_address="0xAttacker"),  # swapped intent...
        intent_hash=good.intent_hash,              # ...but kept the old hash
    )
    assert doctored.verify() is False


def test_intent_is_frozen():
    with pytest.raises(Exception):
        _intent().size = Decimal("1")  # type: ignore[misc]
