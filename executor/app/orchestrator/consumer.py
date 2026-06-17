"""The 'auto' half of semi-auto: consume the advisor's ``edge:signals`` stream and PROPOSE.

Each advisor signal is parsed and, if it's an actionable directional opportunity, run through
``propose_signal`` (form + breakers → ``pending_approval``). Approval stays the deliberate human
step (``approve_and_sign`` / the approve CLI) — the consumer never signs or submits.

The core (``process_message`` / ``run_consumer``) has **no Redis dependency**: ``run_consumer``
loops over an injected async iterable of raw JSON strings, so it's fully testable offline. The
``redis_messages`` adapter (lazy-imports ``redis.asyncio``) is the only Redis-touching code.

Scope: only ``extreme_correction`` directional signals are actionable (``intent_from_signal``);
arb / longshot / gated-zero-size signals are skipped. Dedup is by source signal id (in-memory),
so a re-published signal doesn't pile up duplicate pending intents within a run.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.advised import AdvisedSignalView
from app.orchestrator.workflow import propose_signal
from app.signer.crypto import LocalSigner

logger = logging.getLogger(__name__)

ProcessStatus = Literal["proposed", "skipped_unsupported", "skipped_duplicate", "invalid"]


class ProcessOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ProcessStatus
    intent_id: str | None = None
    signal_id: str | None = None
    reason: str | None = None


def _actionable(advised: AdvisedSignalView) -> bool:
    """Only a directional ``extreme_correction`` with a gate and a positive recommended stake can
    become an intent this slice (``intent_from_signal``)."""
    return (
        advised.strategy == "extreme_correction"
        and advised.kind in ("buy_yes", "buy_no")
        and advised.gate is not None
        and advised.recommended_size_usd > 0
    )


async def process_message(
    session: AsyncSession,
    raw: str,
    *,
    settings: Settings,
    signer: LocalSigner,
    now: datetime,
    intent_id: str,
    seen: set[str],
) -> ProcessOutcome:
    """Parse one ``edge:signals`` message and propose an intent for an actionable directional
    signal. Returns the outcome (proposed / skipped_* / invalid); never signs or submits."""
    try:
        advised = AdvisedSignalView.model_validate_json(raw)
    except ValidationError as exc:
        return ProcessOutcome(status="invalid", reason=str(exc).splitlines()[0])

    if not _actionable(advised):
        return ProcessOutcome(
            status="skipped_unsupported", signal_id=advised.id, reason=f"strategy={advised.strategy}"
        )
    if advised.id in seen:
        return ProcessOutcome(status="skipped_duplicate", signal_id=advised.id)

    result = await propose_signal(
        session, advised, signer=signer, settings=settings, now=now, intent_id=intent_id
    )
    seen.add(advised.id)
    return ProcessOutcome(status="proposed", intent_id=result.intent_id, signal_id=advised.id)


async def run_consumer(
    messages: AsyncIterable[str],
    *,
    settings: Settings,
    signer: LocalSigner,
    sessionmaker: async_sessionmaker[AsyncSession],
    now_fn: Callable[[], datetime],
    id_fn: Callable[[], str],
    seen: set[str] | None = None,
    on_outcome: Callable[[ProcessOutcome], None] | None = None,
) -> None:
    """Drive ``process_message`` over an injected message stream, one DB txn per message. Pure of
    Redis — pass any async iterable of raw JSON strings (``redis_messages`` for the real stream)."""
    seen = seen if seen is not None else set()
    async for raw in messages:
        async with sessionmaker() as session:
            outcome = await process_message(
                session,
                raw,
                settings=settings,
                signer=signer,
                now=now_fn(),
                intent_id=id_fn(),
                seen=seen,
            )
            await session.commit()
        logger.info("consumed signal: %s", outcome.model_dump())
        if on_outcome is not None:
            on_outcome(outcome)


async def redis_messages(settings: Settings) -> AsyncIterable[str]:
    """Subscribe to the advisor's ``signals_channel`` and yield raw JSON payloads. Lazy-imports
    ``redis.asyncio`` so the consumer core stays dependency-free."""
    import redis.asyncio as redis  # lazy: only the runtime adapter needs the client

    client = redis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(settings.signals_channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message["data"]
            yield data.decode() if isinstance(data, bytes) else data
    finally:
        await pubsub.unsubscribe(settings.signals_channel)
        await pubsub.aclose()
        await client.aclose()
