"""The signer's sign entrypoint: policy gate, THEN sign — never the other way around.

This is the trust boundary. ``sign_intent`` runs the independent default-deny policy and only
hands the envelope to the key when the verdict is ``allowed``. A denied intent returns its reasons
and NO signature, so a compromised executor cannot coax out a signature by any field it controls.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.intent import IntentEnvelope
from app.signer.approval import verify_approval_token
from app.signer.crypto import LocalSigner, SignedIntent
from app.signer.policy import SignerPolicy, evaluate_policy


class SignResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    signed: SignedIntent | None  # None iff the policy denied the intent
    rejected_reasons: tuple[str, ...]


def sign_intent(
    envelope: IntentEnvelope,
    policy: SignerPolicy,
    signer: LocalSigner,
    *,
    now: datetime,
    approval_token: str | None = None,
    approval_secret: str = "",
) -> SignResult:
    """Policy-then-sign. The signer verifies the approval token itself (HMAC bound to this exact
    intent hash + TTL); a below-threshold intent needs none. Signs only if every check passes."""
    approval_valid = bool(approval_secret) and verify_approval_token(
        approval_token, envelope.intent_hash, approval_secret, now=now
    )
    verdict = evaluate_policy(envelope, policy, now=now, approval_valid=approval_valid)
    if not verdict.allowed:
        return SignResult(signed=None, rejected_reasons=verdict.reasons)
    return SignResult(signed=signer.sign(envelope), rejected_reasons=())
