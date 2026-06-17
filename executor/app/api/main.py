"""Control API — the human-approval surface for the dashboard (Phase 6-UI).

Read the pending intents the consumer proposed, inspect one with its full audit trail, and approve
it (which signs + dry-run-submits via ``approve_and_sign``). Separate from the signer service
(POST /sign) and the advisor: this is the executor's own small, API-key-guarded HTTP surface that
the web BFF talks to. Stays dry-run while ``EDGE_EXEC_DRY_RUN`` — approving records the signature
and the would-be order, nothing reaches a network.

Approval here is a UI action gated by the shared API key; production should add per-operator auth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import store
from app.db.engine import get_sessionmaker
from app.orchestrator.deps import signer_from_settings
from app.orchestrator.workflow import ApprovalResult, approve_and_sign

app = FastAPI(title="EdgeOracle executor control", version="0.1.0")


class PendingIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    source_signal_id: str
    side: str
    market_id: str
    condition_id: str
    size: Decimal
    max_price: Decimal | None
    notional_usd: Decimal
    created_at: datetime
    expiry: datetime


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    event: str
    actor: str | None
    detail: dict | None


class IntentDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: PendingIntent
    audit: list[AuditEntry]


def _require_api_key(settings: Settings, provided: str | None) -> None:
    if settings.control_api_key and provided != settings.control_api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def _to_pending(intent) -> PendingIntent:
    return PendingIntent(
        intent_id=intent.intent_id,
        source_signal_id=intent.source_signal_id,
        side=intent.side,
        market_id=intent.market_id,
        condition_id=intent.condition_id,
        size=intent.size,
        max_price=intent.max_price,
        notional_usd=intent.notional_usd,
        created_at=intent.created_at,
        expiry=intent.expiry,
    )


async def _session() -> AsyncSession:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/intents/pending", response_model=list[PendingIntent])
async def pending(
    session: AsyncSession = Depends(_session),
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> list[PendingIntent]:
    _require_api_key(settings, x_api_key)
    return [_to_pending(i) for i in await store.load_pending_intents(session)]


@app.get("/intents/{intent_id}", response_model=IntentDetail)
async def intent_detail(
    intent_id: str,
    session: AsyncSession = Depends(_session),
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> IntentDetail:
    _require_api_key(settings, x_api_key)
    intent = await store.load_intent(session, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="intent not found")
    trail = await store.load_audit_trail(session, intent_id)
    audit = [
        AuditEntry(time=r.time, event=r.event, actor=r.actor, detail=r.detail) for r in trail
    ]
    return IntentDetail(intent=_to_pending(intent), audit=audit)


@app.post("/intents/{intent_id}/approve", response_model=ApprovalResult)
async def approve(
    intent_id: str,
    session: AsyncSession = Depends(_session),
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> ApprovalResult:
    _require_api_key(settings, x_api_key)
    signer = signer_from_settings(settings)
    if signer is None:
        raise HTTPException(status_code=503, detail="signer key not configured")
    if not settings.approval_secret:
        raise HTTPException(status_code=503, detail="approval secret not configured")
    result = await approve_and_sign(
        session, intent_id, signer=signer, settings=settings, now=datetime.now(tz=UTC)
    )
    await session.commit()
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail="intent not found")
    return result
