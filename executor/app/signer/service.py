"""The signer's sign entrypoint: policy gate, THEN sign — never the other way around.

This is the trust boundary. ``sign_intent`` runs the independent default-deny policy and only
hands the envelope to the key when the verdict is ``allowed``. A denied intent returns its reasons
and NO signature, so a compromised executor cannot coax out a signature by any field it controls.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.intent import IntentEnvelope
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
    approval_valid: bool,
) -> SignResult:
    """Policy-then-sign. Returns the signature only if every policy check passes."""
    verdict = evaluate_policy(envelope, policy, now=now, approval_valid=approval_valid)
    if not verdict.allowed:
        return SignResult(signed=None, rejected_reasons=verdict.reasons)
    return SignResult(signed=signer.sign(envelope), rejected_reasons=())
