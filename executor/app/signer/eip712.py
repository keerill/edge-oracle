"""EIP-712 typed-data encoding of an intent (pure).

The signer re-derives the digest it signs from the intent fields it received — it never trusts a
caller-supplied digest. We bind the SHA-256 ``intent_hash`` (which commits to every intent field)
plus the on-chain-checkable bounds (chainId, expiry, nonce) into a standards-compliant EIP-712
struct. This keeps the signature recoverable and tamper-evident without prematurely fixing the
Decimal->uint encoding of Polymarket's specific order/tx schema (a later integration).
"""

from __future__ import annotations

from typing import Any

from app.models.intent import IntentEnvelope

_DOMAIN_NAME = "EdgeOracleExecutor"
_DOMAIN_VERSION = "1"

_TYPES: dict[str, list[dict[str, str]]] = {
    "EdgeIntent": [
        {"name": "intentHash", "type": "bytes32"},
        {"name": "chainId", "type": "uint256"},
        {"name": "expiry", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
    ]
}


def intent_typed_data(envelope: IntentEnvelope) -> dict[str, Any]:
    """Build the EIP-712 ``encode_typed_data`` arguments for an intent envelope (deterministic)."""
    intent = envelope.intent
    return {
        "domain_data": {
            "name": _DOMAIN_NAME,
            "version": _DOMAIN_VERSION,
            "chainId": intent.chain_id,
        },
        "message_types": _TYPES,
        "message_data": {
            "intentHash": bytes.fromhex(envelope.intent_hash),
            "chainId": intent.chain_id,
            "expiry": int(intent.expiry.timestamp()),
            "nonce": intent.nonce,
        },
    }
