"""The execution pipeline: advisor opportunity → intent → breakers → approval → sign → submit.

Composes the already-tested pure pieces (``intent_from_signal``, ``breakers.evaluate``,
``signer.sign_intent``, ``relay.submit``) into one auditable flow, writing an immutable audit row
at every transition (``formed`` → ``breaker_rejected`` | ``pending_approval`` → ``approved`` →
``signed`` → ``submitted``). Dependency-injected (session, signer, policy, limits, state, clock,
ids) so it stays testable the way the advisor's engines are — no globals, no hidden clock.

Two safety properties this slice guarantees:
  * **Dry-run never broadcasts** — the submit step records the payload and stops (``relay.submit``
    raises if asked to go live without the real CLOB schema/credentials). Dry-run is also exempt
    from the master switch (it's a simulation); real execution still needs ``EDGE_EXEC_ENABLED``.
  * **Semi-auto** — with ``require_approval_for_all`` (the operator's choice), EVERY trade needs a
    valid human approval token before the signer is ever called; without it the flow stops at
    ``pending_approval`` and produces no signature.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.breakers.checks import BreakerLimits, BreakerState, evaluate
from app.db import store
from app.models.advised import AdvisedSignalView
from app.models.intent import IntentEnvelope
from app.orchestrator.intents import intent_from_signal
from app.relay.client import SubmitResult, submit
from app.signer.approval import verify_approval_token
from app.signer.crypto import LocalSigner, SignedIntent
from app.signer.policy import SignerPolicy
from app.signer.service import sign_intent

PipelineStatus = Literal[
    "breaker_rejected", "pending_approval", "signer_rejected", "submitted"
]


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    status: PipelineStatus
    reasons: tuple[str, ...] = ()
    signed: SignedIntent | None = None
    submission: SubmitResult | None = None


async def execute_signal(
    session: AsyncSession,
    advised: AdvisedSignalView,
    *,
    signer: LocalSigner,
    policy: SignerPolicy,
    limits: BreakerLimits,
    state: BreakerState,
    notional_usd: Decimal,
    clob_exchange_address: str,
    now: datetime,
    expiry: datetime,
    intent_id: str,
    dry_run: bool = True,
    require_approval_for_all: bool = True,
    approval_token: str | None = None,
    approval_secret: str = "",
) -> ExecutionResult:
    """Run one advisor signal through the full execution flow, auditing every transition.

    Returns at the first terminal state: ``breaker_rejected`` (a limit breached),
    ``pending_approval`` (manual mode, no valid token), ``signer_rejected`` (the independent signer
    policy denied it), or ``submitted`` (signed + dry-run submit recorded). The caller owns the
    transaction (commit after)."""
    # Form the intent (allocates the authoritative on-chain nonce under FOR UPDATE).
    nonce = await store.allocate_nonce(session, signer.address, policy.chain_id)
    intent = intent_from_signal(
        advised,
        notional_usd=notional_usd,
        nonce=nonce,
        clob_exchange_address=clob_exchange_address,
        max_slippage=limits.max_slippage,
        now=now,
        expiry=expiry,
        intent_id=intent_id,
    )
    envelope = IntentEnvelope.seal(intent, approval_token)
    await store.insert_intent(session, intent, envelope.intent_hash)
    await store.append_audit(
        session,
        intent_id=intent_id,
        event="formed",
        actor="system",
        time=now,
        detail={"source_signal_id": advised.id, "dry_run": dry_run},
    )

    # Breakers. Dry-run is exempt from the master switch (it's a simulation, nothing executes);
    # a real run still requires EDGE_EXEC_ENABLED via limits.enabled.
    effective = limits.model_copy(update={"enabled": limits.enabled or dry_run})
    decision = evaluate(intent, state, effective)
    if not decision.approved:
        await store.append_audit(
            session,
            intent_id=intent_id,
            event="breaker_rejected",
            actor="system",
            time=now,
            detail={"rejections": list(decision.rejections)},
        )
        return ExecutionResult(
            intent_id=intent_id, status="breaker_rejected", reasons=decision.rejections
        )

    # Semi-auto approval: every trade needs a valid human token before we ever sign.
    approval_valid = bool(approval_secret) and verify_approval_token(
        approval_token, envelope.intent_hash, approval_secret, now=now
    )
    if require_approval_for_all and not approval_valid:
        await store.append_audit(
            session,
            intent_id=intent_id,
            event="pending_approval",
            actor="system",
            time=now,
            detail={"mode": "require_approval_for_all"},
        )
        return ExecutionResult(intent_id=intent_id, status="pending_approval")
    if approval_valid:
        await store.append_audit(
            session, intent_id=intent_id, event="approved", actor="human", time=now
        )

    # Sign — the independent default-deny policy runs again inside sign_intent.
    result = sign_intent(
        envelope,
        policy,
        signer,
        now=now,
        approval_token=approval_token,
        approval_secret=approval_secret,
    )
    if result.signed is None:
        await store.append_audit(
            session,
            intent_id=intent_id,
            event="signer_rejected",
            actor="system",
            time=now,
            detail={"reasons": list(result.rejected_reasons)},
        )
        return ExecutionResult(
            intent_id=intent_id, status="signer_rejected", reasons=result.rejected_reasons
        )
    await store.append_audit(
        session,
        intent_id=intent_id,
        event="signed",
        actor="system",
        time=now,
        detail={"signer_address": result.signed.signer_address},
    )

    # Submit — dry-run records the payload and stops; nothing reaches a network.
    submission = submit(intent, result.signed, dry_run=dry_run)
    await store.append_audit(
        session,
        intent_id=intent_id,
        event="submitted",
        actor="system",
        time=now,
        tx_hash=submission.order_ref,
        detail={"dry_run": dry_run, "status": submission.status},
    )
    return ExecutionResult(
        intent_id=intent_id, status="submitted", signed=result.signed, submission=submission
    )
