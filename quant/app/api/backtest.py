"""GET /backtest — a deterministic replay report over the stored price history.

The replay needs market outcomes (``resolutions``); resolution ingestion is a later slice, so
they are loaded from an optional configured JSON path (``EDGE_BACKTEST_RESOLUTIONS_PATH``). When
unset, the endpoint degrades to a well-formed zero-bet report so the dashboard always renders.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_app_sessionmaker, get_app_settings
from app.backtest.engine import _load_resolutions, run_backtest_once
from app.config import Settings
from app.models.backtest import BacktestResult

router = APIRouter(tags=["backtest"])


@router.get("/backtest", response_model=BacktestResult)
async def get_backtest(
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_app_sessionmaker),
    settings: Settings = Depends(get_app_settings),
) -> BacktestResult:
    """Replay stored quotes against the configured resolutions feed (empty -> zero-bet report)."""
    path = settings.backtest_resolutions_path
    if path and os.path.exists(path):
        resolutions = await run_in_threadpool(_load_resolutions, path)
    else:
        resolutions = {}
    return await run_backtest_once(sessionmaker, settings, resolutions)
