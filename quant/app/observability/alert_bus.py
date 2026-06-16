"""Thin async alert bus — publish an Alert to Redis + Sentry, and count it.

Mirrors ``redis_bus.publish_signal``: Pydantic renders ``Decimal`` as a JSON string (same wire
contract the web Zod boundary expects). Also bumps the ``edge_alerts_total`` metric and forwards
to Sentry (a no-op unless a DSN is configured).
"""

from __future__ import annotations

from app.models.alert import Alert
from app.observability import sentry
from app.observability.metrics import ALERTS


async def publish_alert(redis, channel: str, alert: Alert) -> None:
    """Publish one alert as JSON to ``channel``; capture it to Sentry; bump the counter."""
    ALERTS.labels(alert.kind, alert.severity).inc()
    sentry.capture_alert(alert)
    await redis.publish(channel, alert.model_dump_json())
