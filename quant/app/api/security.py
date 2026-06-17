"""API hardening: shared-secret auth, an in-memory rate limiter, and CORS origins.

The advisor API was fully open. These add defense in depth:
  * ``require_api_key`` — when ``EDGE_API_KEY`` is set, the advisor routes require a matching
    ``X-API-Key`` header (the web BFF sends it). Unset -> open (local dev). ``/health`` and
    ``/metrics`` are never gated (probes/scrapers).
  * ``RateLimiter`` — a per-client sliding-window limiter (pure logic, unit-tested); wired as an
    HTTP middleware, disabled when ``EDGE_RATE_LIMIT_PER_MIN`` is 0.
  * CORS origins parsed from ``EDGE_CORS_ORIGINS`` (csv). The browser hits the Next BFF, not quant,
    so this is a safety net rather than the primary control.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from app.api.deps import get_app_settings
from app.config import Settings


async def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Reject the request when an API key is configured and the header doesn't match."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


class RateLimiter:
    """Per-key sliding-window limiter. ``allow(key, now)`` records the hit and returns whether
    it's within ``limit`` requests per ``window_s``. Pure logic (the clock is injected), so the
    window edges are unit-testable; the HTTP middleware passes the real wall clock."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        cutoff = now - self.window_s
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


def cors_origins(settings: Settings) -> list[str]:
    """Allowed CORS origins from the csv knob (empty -> none)."""
    return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
