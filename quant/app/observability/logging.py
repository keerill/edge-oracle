"""Structured JSON logging — one JSON object per line to stdout.

Every long-running entrypoint (the FastAPI lifespan + each CLI) calls
``configure_logging(service)`` once so all logs share a schema: the five base fields
(``ts``/``level``/``logger``/``msg``/``service``) plus any ``extra=`` kwargs and a rendered
``exc`` on errors. Set ``EDGE_LOG_JSON=false`` for the human-readable format when tailing
locally. The web service emits the same five base fields (see ``web/src/lib/logger.ts``).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Standard LogRecord attributes; everything else in ``record.__dict__`` is an ``extra=`` field.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}

_HUMAN_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object tagged with ``service``."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": self.service,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # default=str so arbitrary extras (Decimal, datetime, ...) never break a log line.
        return json.dumps(payload, default=str)


def configure_logging(service: str) -> None:
    """Install a single stdout handler on the root logger (replaces existing handlers).

    Honors ``EDGE_LOG_LEVEL`` (default INFO) and ``EDGE_LOG_JSON`` (default true; false
    falls back to the human format the CLIs used before). Replacing handlers keeps a
    re-invocation (e.g. a CLI that also imports the FastAPI app) from double-logging.
    """
    from app.config import get_settings

    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(service) if settings.log_json else logging.Formatter(_HUMAN_FORMAT)
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
