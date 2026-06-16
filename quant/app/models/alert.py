"""Alert model — one operational alert (frozen, Decimal-native).

Produced by the pure predicates in ``app.observability.alerts``, published to the Redis
``edge:alerts`` channel and captured to Sentry by the alert bus, then surfaced as a dashboard
toast. Pydantic renders ``Decimal`` as a JSON **string** — the same Decimal->string wire
contract as ``AdvisedSignal``, so the web Zod boundary coerces it the same way.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

AlertKind = Literal["ws_drop", "drawdown_breach", "calibration_drift"]
AlertSeverity = Literal["info", "warning", "error"]


class Alert(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: AlertKind
    severity: AlertSeverity
    title: str
    detail: str
    value: Decimal | None  # observed metric (drawdown fraction, drift gap, drop count)
    threshold: Decimal | None  # the breached threshold (None for thresholdless alerts)
    time: datetime  # UTC, injected by the caller (test seam)
