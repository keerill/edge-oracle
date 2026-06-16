"""Sentry error capture — armed only when ``EDGE_SENTRY_DSN`` is set.

``init_sentry`` is a clean no-op without a DSN (the dev/test default), so nothing phones
home locally. When a DSN is configured, the ``LoggingIntegration`` turns every
``logger.error`` / ``logger.exception`` already in the loops into a Sentry event for free.
The DSN is a SECRET: supply it via env / a secret manager, never commit it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.alert import Alert


def init_sentry(service: str) -> None:
    """Initialize Sentry for this process, tagged with ``service``. No-op without a DSN."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.sentry_dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        # Errors, not performance traces — keep it cheap (advisor, not a hot service).
        traces_sample_rate=0.0,
        integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
    )
    sentry_sdk.set_tag("service", service)


def capture_alert(alert: Alert) -> None:
    """Send an alert to Sentry as a message event. No-op when Sentry isn't initialized.

    Alert severities ("info"/"warning"/"error") are also valid Sentry levels, so the severity
    maps straight through.
    """
    import sentry_sdk

    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("alert_kind", alert.kind)
        if alert.value is not None:
            scope.set_extra("value", str(alert.value))
        if alert.threshold is not None:
            scope.set_extra("threshold", str(alert.threshold))
        sentry_sdk.capture_message(f"{alert.title}: {alert.detail}", level=alert.severity)
