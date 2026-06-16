"""Sentry init — must be a clean no-op when no DSN is configured.

Local dev and the whole test suite run without ``EDGE_SENTRY_DSN``; ``init_sentry`` must
not raise and must not arm the SDK (so nothing tries to phone home in tests/dev). The
DSN-set path is verified manually in dev (see PROGRESS.md verification).
"""

from __future__ import annotations

import sentry_sdk

from app.config import get_settings
from app.observability.sentry import init_sentry


def test_init_sentry_is_noop_without_dsn() -> None:
    get_settings.cache_clear()
    try:
        init_sentry("quant.test")  # default settings -> sentry_dsn is None
        assert not sentry_sdk.get_client().is_active()
    finally:
        get_settings.cache_clear()
