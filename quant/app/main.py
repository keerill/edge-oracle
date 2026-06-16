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
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api import backtest, calibration, signals
from app.config import get_settings
from app.observability.logging import configure_logging

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging("quant")
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

app.include_router(signals.router)
app.include_router(calibration.router)
app.include_router(backtest.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
