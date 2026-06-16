"""Thin async Redis pub/sub for the live signal stream.

The engine publishes ``AdvisedSignal`` JSON to a single channel; the web SSE endpoint
subscribes and fans out to the dashboard. Pydantic v2 serializes ``Decimal`` to a JSON
**string** — the same Decimal->string wire contract the REST API uses (and the web Zod
boundary already coerces), so the SSE payload is byte-for-byte a ``/signals`` row.
"""

from __future__ import annotations

from app.models.advisor import AdvisedSignal


async def publish_signal(redis, channel: str, advised: AdvisedSignal) -> None:
    """Publish one advised signal as JSON to ``channel`` (no float in the money path)."""
    await redis.publish(channel, advised.model_dump_json())
