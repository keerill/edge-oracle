"""The signer service — its ONLY HTTP surface is POST /sign.

Deployed as a separate, isolated service (its own process / network policy / IAM). It is the sole
holder of key material; everything calling it is treated as hostile. It re-verifies the intent and
runs its own default-deny policy (``app.signer.service.sign_intent``) before the key ever signs.
Denied intents get 403 + reasons and NO signature. ``/health`` is unauthenticated for probes.

This offline build uses a local testnet key; production swaps ``LocalSigner`` for an AWS KMS
signer behind the same interface (key never exported).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.models.intent import IntentEnvelope
from app.signer.crypto import SignedIntent
from app.signer.deps import policy_from_settings, signer_from_settings
from app.signer.service import sign_intent

app = FastAPI(title="EdgeOracle signer", version="0.1.0")


class SignRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    envelope: IntentEnvelope
    approval_token: str | None = None


@app.post("/sign", response_model=SignedIntent)
def sign(request: SignRequest) -> SignedIntent:
    settings = get_settings()
    signer = signer_from_settings(settings)
    if signer is None:
        raise HTTPException(status_code=503, detail="signer key not configured")
    result = sign_intent(
        request.envelope,
        policy_from_settings(settings),
        signer,
        now=datetime.now(tz=UTC),
        approval_token=request.approval_token,
        approval_secret=settings.approval_secret,
    )
    if result.signed is None:
        # Default-deny: no signature, surface the policy reasons.
        raise HTTPException(status_code=403, detail={"rejected": list(result.rejected_reasons)})
    return result.signed


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
