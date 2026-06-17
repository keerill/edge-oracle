"""Dry-run submit client — records the payload, never broadcasts. Live path is refused."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.intent import Intent
from app.relay.client import notional_within, submit
from app.signer.crypto import SignedIntent

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2100, 1, 1, tzinfo=UTC)


def _intent() -> Intent:
    return Intent(
        intent_id="i-1", created_at=NOW, expiry=EXP, source_signal_id="s1",
        action="clob_order", chain_id=137, market_id="m1", condition_id="c1",
        side="buy_no", size=Decimal("400"), max_price=Decimal("0.30"),
        max_slippage=Decimal("0.01"), notional_usd=Decimal("40"),
        to_address="0xExchange", token_id=None, approve_spender=None,
        approve_amount=None, nonce=7,
    )


def _signed(intent: Intent) -> SignedIntent:
    return SignedIntent(
        intent_id=intent.intent_id, intent_hash="0xhash", signer_address="0xSigner",
        r="0x1", s="0x2", v=27, signature="0xsig",
    )


def test_dry_run_records_payload_without_broadcast():
    intent = _intent()
    result = submit(intent, _signed(intent), dry_run=True)
    assert result.status == "dry_run"
    assert result.order_ref == "0xhash"  # the intent hash is the dry-run handle
    # Payload is float-free (Decimals serialized as strings) and carries the signature.
    assert result.payload["size"] == "400"
    assert result.payload["notional_usd"] == "40"
    assert result.payload["signature"] == "0xsig"
    assert isinstance(result.payload["size"], str)


def test_live_submission_is_refused():
    intent = _intent()
    with pytest.raises(NotImplementedError):
        submit(intent, _signed(intent), dry_run=False)


def test_notional_within_guard():
    assert notional_within(Decimal("40"), Decimal("100")) is True
    assert notional_within(Decimal("100"), Decimal("100")) is True  # inclusive
    assert notional_within(Decimal("0"), Decimal("100")) is False
    assert notional_within(Decimal("101"), Decimal("100")) is False
