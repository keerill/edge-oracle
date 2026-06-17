"""Two-step semi-auto workflow: propose an intent, then a human approves it (dry-run).

  1. ``propose_signal`` runs the pipeline with NO token: it forms + persists the intent, runs the
     breakers, and (manual mode) stops at ``pending_approval`` — nothing is signed.
  2. ``approve_and_sign`` is the human step: it loads the SAME stored intent (so the hash matches),
     mints an approval token bound to that hash, records the approval (token HASH only), then signs
     and dry-run-submits.

Splitting it this way is what makes "approve cheap, swap expensive" impossible: the token is minted
against the persisted intent's hash, and ``approve_and_sign`` never re-forms the intent (no new
nonce). All composition (signer/policy/limits/state) comes from ``deps``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import store
from app.models.advised import AdvisedSignalView
from app.models.intent import IntentEnvelope, compute_intent_hash
from app.orchestrator import deps
from app.orchestrator.pipeline import ExecutionResult, execute_signal
from app.relay.client import SubmitResult, submit
from app.signer.approval import mint_approval_token
from app.signer.crypto import LocalSigner, SignedIntent
from app.signer.service import sign_intent

ApprovalStatus = Literal["not_found", "signer_rejected", "submitted"]


class ApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    status: ApprovalStatus
    reasons: tuple[str, ...] = ()
    signed: SignedIntent | None = None
    submission: SubmitResult | None = None


async def propose_signal(
    session: AsyncSession,
    advised: AdvisedSignalView,
    *,
    signer: LocalSigner,
    settings: Settings,
    now: datetime,
    intent_id: str,
    notional_usd: Decimal | None = None,
) -> ExecutionResult:
    """Step 1: form + persist the intent and run the breakers. In manual mode (the default) this
    stops at ``pending_approval`` with no signature. ``notional_usd`` defaults to the advisor's
    recommended stake."""
    notional = advised.recommended_size_usd if notional_usd is None else notional_usd
    return await execute_signal(
        session,
        advised,
        signer=signer,
        policy=deps.policy_from_settings(settings),
        limits=deps.build_limits(settings),
        state=deps.build_state(settings),
        notional_usd=notional,
        clob_exchange_address=deps.clob_exchange_address(settings),
        now=now,
        expiry=now + timedelta(seconds=settings.intent_ttl_s),
        intent_id=intent_id,
        dry_run=settings.dry_run,
        require_approval_for_all=settings.require_approval_for_all,
        approval_token=None,
        approval_secret=settings.approval_secret,
    )


async def approve_and_sign(
    session: AsyncSession,
    intent_id: str,
    *,
    signer: LocalSigner,
    settings: Settings,
    now: datetime,
    approver: str = "human",
) -> ApprovalResult:
    """Step 2: the human approval. Loads the stored intent, mints a token bound to its exact hash,
    persists the approval (hash only), then signs + dry-run-submits."""
    intent = await store.load_intent(session, intent_id)
    if intent is None:
        return ApprovalResult(intent_id=intent_id, status="not_found")

    intent_hash = compute_intent_hash(intent)
    expires_at = now + timedelta(seconds=settings.approval_token_ttl_s)
    token = mint_approval_token(intent_hash, expires_at, settings.approval_secret)
    await store.insert_approval(
        session,
        intent_id=intent_id,
        approval_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        threshold_usd=settings.approval_threshold_usd,
        approver=approver,
        granted_at=now,
        expires_at=expires_at,
    )
    await store.append_audit(
        session, intent_id=intent_id, event="approved", actor=approver, time=now
    )

    envelope = IntentEnvelope.seal(intent, token)
    result = sign_intent(
        envelope,
        deps.policy_from_settings(settings),
        signer,
        now=now,
        approval_token=token,
        approval_secret=settings.approval_secret,
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
        return ApprovalResult(
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

    submission = submit(intent, result.signed, dry_run=settings.dry_run)
    await store.append_audit(
        session,
        intent_id=intent_id,
        event="submitted",
        actor="system",
        time=now,
        tx_hash=submission.order_ref,
        detail={"dry_run": settings.dry_run, "status": submission.status},
    )
    return ApprovalResult(
        intent_id=intent_id, status="submitted", signed=result.signed, submission=submission
    )
