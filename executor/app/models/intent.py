"""The trade ``Intent`` — the ONLY thing the signer ever signs — and its tamper-evident envelope.

Frozen and ``Decimal``-native, mirroring the advisor's money models (``Decimal`` -> JSON
**string**, never float). The ``intent_hash`` binds the exact fields: the signer recomputes
it and refuses to sign on any mismatch, so a recipient/size/price tampered between the
executor and the signer is rejected at the trust boundary. No keys, no I/O here — this module
is pure data + a hash.

The canonical serialization is ``json.dumps(model_dump(mode="json"), sort_keys=True)`` with
compact separators: ``mode="json"`` renders every ``Decimal`` as its exact string and the
datetimes as ISO-8601, and ``sort_keys`` makes the hash independent of field declaration
order — so the executor and the signer hash byte-for-byte identically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# On-chain / off-chain action the intent encodes. ``clob_order`` is an off-chain EIP-712
# order to Polymarket's CLOB; the ``ctf_*`` actions are on-chain Conditional-Tokens ops;
# ``erc20_approve`` sets an EXACT (never infinite) USDC allowance to an allowlisted spender.
ActionType = Literal["clob_order", "ctf_split", "ctf_merge", "ctf_redeem", "erc20_approve"]

Side = Literal["buy_yes", "buy_no", "sell_yes", "sell_no", "split", "merge", "redeem"]


class Intent(BaseModel):
    """A single, precisely-specified trade action. One on-chain/off-chain action per intent;
    a multi-leg arb is an ordered list of intents, each with its own nonce and audit row."""

    model_config = ConfigDict(frozen=True)

    intent_id: str  # uuid-ish; also the audit correlation id
    created_at: datetime
    expiry: datetime  # hard deadline; signer/relay reject once past
    source_signal_id: str  # provenance: the AdvisedSignal.id this derives from

    action: ActionType
    chain_id: int = 137  # Polygon; the signer checks this is pinned
    market_id: str
    condition_id: str

    side: Side
    size: Decimal  # shares (clob) or complete sets (ctf), exact
    max_price: Decimal | None  # worst acceptable per-share price (clob); None for ctf ops
    max_slippage: Decimal  # hard slippage cap; the tx/order enforces min-out/max-in
    notional_usd: Decimal  # for breaker / approval-threshold checks

    to_address: str  # target contract (allowlisted: CLOB Exchange / NegRisk / CTF)
    token_id: str | None  # ERC-1155 position id when known
    approve_spender: str | None  # erc20_approve only
    approve_amount: Decimal | None  # erc20_approve only — EXACT, never infinite
    nonce: int  # replay protection (on-chain action nonce)


def canonical_intent_json(intent: Intent) -> str:
    """Deterministic, float-free serialization used as the hash preimage. ``sort_keys`` makes
    it independent of field order so executor and signer agree byte-for-byte."""
    return json.dumps(
        intent.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )


def compute_intent_hash(intent: Intent) -> str:
    """SHA-256 hex of the canonical intent JSON — the binding the signer re-verifies."""
    return hashlib.sha256(canonical_intent_json(intent).encode("utf-8")).hexdigest()


class IntentEnvelope(BaseModel):
    """What crosses the executor -> signer boundary: the intent plus its binding hash and an
    optional human-approval token (required for above-threshold intents). ``verify`` recomputes
    the hash from the carried intent, so a swapped intent with a stale hash is detected."""

    model_config = ConfigDict(frozen=True)

    intent: Intent
    intent_hash: str
    approval_token: str | None = Field(default=None)

    @classmethod
    def seal(cls, intent: Intent, approval_token: str | None = None) -> IntentEnvelope:
        return cls(
            intent=intent,
            intent_hash=compute_intent_hash(intent),
            approval_token=approval_token,
        )

    def verify(self) -> bool:
        """True iff the carried ``intent_hash`` matches a fresh hash of ``intent``."""
        return self.intent_hash == compute_intent_hash(self.intent)
