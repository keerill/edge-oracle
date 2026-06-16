"""Sentry error capture — armed only when ``EDGE_SENTRY_DSN`` is set.

``init_sentry`` is a clean no-op without a DSN (the dev/test default), so nothing phones
home locally. When a DSN is configured, the ``LoggingIntegration`` turns every
``logger.error`` / ``logger.exception`` already in the loops into a Sentry event for free.
The DSN is a SECRET: supply it via env / a secret manager, never commit it.
"""

from __future__ import annotations

import logging


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
