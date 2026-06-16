"""Prometheus metrics — registration, the FastAPI /metrics exposition, and increments.

Offline: the /metrics mount serves the default registry, so no DB/lifespan is needed (the
ASGITransport drives the app the same way the DB-gated API tests do, minus the overrides).
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.main import app
from app.observability.metrics import SIGNALS


async def test_metrics_endpoint_exposes_edge_families() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    for family in (
        "edge_http_request_duration_seconds",
        "edge_poller_scans_total",
        "edge_poller_last_success_timestamp_seconds",
        "edge_signals_total",
        "edge_ws_up",
        "edge_alerts_total",
    ):
        assert family in body, f"missing metric family: {family}"


def test_signals_counter_increments() -> None:
    labels = {"strategy": "set_arb", "source": "stream"}
    before = REGISTRY.get_sample_value("edge_signals_total", labels) or 0.0
    SIGNALS.labels("set_arb", "stream").inc(3)
    after = REGISTRY.get_sample_value("edge_signals_total", labels) or 0.0
    assert after - before == 3.0
