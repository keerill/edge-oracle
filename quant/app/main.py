"""FastAPI app: health check, the advisor read API, and an optional lifespan poller.

The poller is started here ONLY when ``EDGE_RUN_POLLER_ON_STARTUP=true``. By default
the app is quiet and the poller is run as a standalone process
(``python -m app.ingestion.scanner``). The advisor REST surface (``/signals``,
``/calibration``, ``/backtest``) is always mounted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import backtest, calibration, config, positions, signals
from app.api.security import RateLimiter, cors_origins, require_api_key
from app.config import get_settings
from app.observability.logging import configure_logging
from app.observability.metrics import render_latest
from app.observability.sentry import init_sentry

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging("quant")
    init_sentry("quant")
    task: asyncio.Task[None] | None = None
    if settings.run_poller_on_startup:
        # Imported lazily so the app (and tests) don't require DB/clients unless
        # the poller is explicitly enabled.
        from app.ingestion.scanner import run_poller_forever

        logger.info("starting background poller on app startup")
        task = asyncio.create_task(run_poller_forever())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="EdgeOracle quant", version="0.1.0", lifespan=lifespan)

_settings = get_settings()

_origins = cors_origins(_settings)
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

if _settings.rate_limit_per_min > 0:
    _limiter = RateLimiter(_settings.rate_limit_per_min, window_s=60.0)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        # /health and /metrics are exempt (probes / scrapers).
        if request.url.path not in ("/health", "/metrics"):
            client = request.client.host if request.client else "unknown"
            if not _limiter.allow(client, time.monotonic()):
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        return await call_next(request)

# Advisor routes require the API key when EDGE_API_KEY is set (the web BFF sends it).
_guard = [Depends(require_api_key)]
app.include_router(signals.router, dependencies=_guard)
app.include_router(calibration.router, dependencies=_guard)
app.include_router(backtest.router, dependencies=_guard)
app.include_router(config.router, dependencies=_guard)
app.include_router(positions.router, dependencies=_guard)

@app.get("/metrics")
def metrics() -> Response:
    """Prometheus exposition for the API process (latency + any in-process families)."""
    body, content_type = render_latest()
    return Response(body, media_type=content_type)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
