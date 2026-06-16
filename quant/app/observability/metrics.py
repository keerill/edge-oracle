"""Prometheus metrics — a single process-wide registry of the families we scrape.

The FastAPI app exposes the default registry at ``GET /metrics`` (``metrics_asgi_app``);
each standalone CLI calls ``start_metrics_server`` to expose its own (separate process =>
separate registry). Increments live in the I/O / loop layer (http, scanner, signals,
streaming, monitor) — never inside the pure math or the ``run_*_once`` / ``run_stream``
seams, so the existing tests stay green.

Counters are named without the ``_total`` suffix; prometheus_client appends it on exposition
(``edge_signals`` -> ``edge_signals_total``).
"""

from __future__ import annotations

import logging

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

logger = logging.getLogger(__name__)

# --- Polymarket HTTP latency ---------------------------------------------------
HTTP_REQUEST_DURATION = Histogram(
    "edge_http_request_duration_seconds",
    "Polymarket HTTP request latency in seconds",
    labelnames=("host", "outcome"),  # outcome: ok | retry | error
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# --- Ingestion poller health ---------------------------------------------------
POLLER_SCANS = Counter(
    "edge_poller_scans", "Completed ingestion scan cycles", ("status",)  # ok | error
)
POLLER_SCAN_DURATION = Histogram(
    "edge_poller_scan_duration_seconds", "Wall time of one ingestion scan cycle"
)
POLLER_LAST_SUCCESS_TS = Gauge(
    "edge_poller_last_success_timestamp_seconds", "Unix time of the last successful scan"
)
POLLER_QUOTES = Counter("edge_poller_quotes_written", "Quotes persisted by the poller")

# --- Signals -------------------------------------------------------------------
SIGNALS = Counter(
    "edge_signals", "Signals detected", ("strategy", "source")  # source: scan | stream
)

# --- CLOB WebSocket health -----------------------------------------------------
WS_CONNECTS = Counter("edge_ws_connects", "CLOB WS successful connects")
WS_DROPS = Counter("edge_ws_drops", "CLOB WS drops / reconnects")
WS_UP = Gauge("edge_ws_up", "1 while the CLOB WS is connected, else 0")

# --- Alerts --------------------------------------------------------------------
ALERTS = Counter("edge_alerts", "Alerts emitted", ("kind", "severity"))

def render_latest() -> tuple[bytes, str]:
    """Render the default registry in Prometheus text format: ``(body, content_type)``."""
    return generate_latest(), CONTENT_TYPE_LATEST


def start_metrics_server(port: int) -> None:
    """Best-effort: expose this process's metrics on ``port``.

    A bind failure (e.g. another CLI already holds the port in dev) logs a warning and is
    swallowed — metrics must never kill a loop.
    """
    try:
        start_http_server(port)
        logger.info("metrics server listening", extra={"port": port})
    except OSError as exc:  # noqa: BLE001 - best-effort; never fatal
        logger.warning(
            "metrics server failed to bind", extra={"port": port, "err": repr(exc)}
        )
