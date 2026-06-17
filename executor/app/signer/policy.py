"""The signer's INDEPENDENT default-deny policy — the crown-jewel control of the threat model.

The signer assumes the executor host is compromised, so it never trusts the caller: before it
signs anything it re-derives the intent hash and checks the fully-decoded intent against its own
policy (loaded from a source the executor can't write). Anything it cannot fully vouch for is
DENIED. This module is PURE (no keys, no crypto, no I/O) so every deny path is unit-tested; the
crypto signing layer (EIP-712/1559 digest + (r,s,v)) consumes a verdict from here and only signs
when ``allowed`` is true.

Controls (each a deny reason): intent-hash integrity, chainId pin, expiry, action allowlist,
target-contract allowlist, per-tx notional cap, slippage cap, exact (never infinite/zero)
ERC-20 allowance to an allowlisted spender, and a required approval token above the threshold.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.intent import ActionType, IntentEnvelope, compute_intent_hash

ZERO = Decimal(0)


class SignerPolicy(BaseModel):
    """The signer-owned policy. Immutable from the executor's request path — changes go through a
    separate admin path (not modeled here). Allowances above ``max_notional_usd`` are treated as
    de-facto infinite and rejected."""

    model_config = ConfigDict(frozen=True)

    chain_id: int
    allowed_actions: frozenset[ActionType]
    allowlisted_contracts: frozenset[str]
    allowlisted_spenders: frozenset[str]
    max_notional_usd: Decimal
    max_slippage: Decimal
    approval_threshold_usd: Decimal


class PolicyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reasons: tuple[str, ...]


def evaluate_policy(
    envelope: IntentEnvelope,
    policy: SignerPolicy,
    *,
    now: datetime,
    approval_valid: bool,
) -> PolicyVerdict:
    """Default-deny: return every reason the intent must NOT be signed; ``allowed`` iff none."""
    reasons: list[str] = []
    intent = envelope.intent

    # Integrity first: the carried hash must match a fresh hash of the intent (tamper detection).
    if envelope.intent_hash != compute_intent_hash(intent):
        reasons.append("intent hash mismatch (tampered envelope)")

    if intent.chain_id != policy.chain_id:
        reasons.append(f"wrong chain: {intent.chain_id} != {policy.chain_id}")

    if intent.expiry <= now:
        reasons.append(f"intent expired at {intent.expiry}")

    if intent.action not in policy.allowed_actions:
        reasons.append(f"action not allowlisted: {intent.action}")

    if intent.to_address not in policy.allowlisted_contracts:
        reasons.append(f"target contract not allowlisted: {intent.to_address}")

    if intent.notional_usd > policy.max_notional_usd:
        reasons.append(f"notional over cap: {intent.notional_usd} > {policy.max_notional_usd}")

    if intent.max_slippage > policy.max_slippage:
        reasons.append(f"slippage over cap: {intent.max_slippage} > {policy.max_slippage}")

    if intent.action == "erc20_approve":
        reasons.extend(_check_allowance(intent, policy))

    if intent.notional_usd > policy.approval_threshold_usd and not approval_valid:
        reasons.append(
            f"approval required: {intent.notional_usd} > {policy.approval_threshold_usd}"
        )

    return PolicyVerdict(allowed=not reasons, reasons=tuple(reasons))


def _check_allowance(intent, policy: SignerPolicy) -> list[str]:
    """ERC-20 approve must be to an allowlisted spender for an EXACT, bounded, positive amount —
    never None/zero and never the de-facto-infinite (above the per-tx cap) approval anti-pattern."""
    out: list[str] = []
    if intent.approve_spender not in policy.allowlisted_spenders:
        out.append(f"approve spender not allowlisted: {intent.approve_spender}")
    amount = intent.approve_amount
    if amount is None or amount <= ZERO:
        out.append("approve amount must be a positive, exact allowance")
    elif amount > policy.max_notional_usd:
        out.append(f"allowance too large (de-facto infinite): {amount} > {policy.max_notional_usd}")
    return out
