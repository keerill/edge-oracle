"""Alert bus — publishes the alert JSON to the channel and counts it.

Offline: a capturing fake redis (an object with an async ``publish``) records the call; Sentry
is a no-op because no DSN is configured (so ``capture_alert`` returns early). Same
Decimal->string wire contract as ``publish_signal``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from prometheus_client import REGISTRY

from app.models.alert import Alert
from app.observability.alert_bus import publish_alert

AT = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def _alert() -> Alert:
    return Alert(
        kind="drawdown_breach",
        severity="error",
        title="Drawdown threshold breached",
        detail="max drawdown 0.5 >= threshold 0.2",
        value=Decimal("0.5"),
        threshold=Decimal("0.2"),
        time=AT,
    )


async def test_publish_alert_publishes_json_and_counts() -> None:
    redis = _FakeRedis()
    alert = _alert()
    labels = {"kind": "drawdown_breach", "severity": "error"}
    before = REGISTRY.get_sample_value("edge_alerts_total", labels) or 0.0

    await publish_alert(redis, "edge:alerts", alert)

    # exact JSON payload (Decimal -> string), on the right channel
    assert redis.published == [("edge:alerts", alert.model_dump_json())]
    after = REGISTRY.get_sample_value("edge_alerts_total", labels) or 0.0
    assert after - before == 1.0
