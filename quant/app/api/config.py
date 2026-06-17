"""GET /config and PUT /config — the operator's personal bankroll & risk preferences.

The dashboard reads the effective config (the persisted row, or the env defaults when none
has been saved) and writes updates here. The config is applied server-side to the advisor
sizing, so changing the bankroll re-sizes every signal on the next fetch.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_session
from app.config import Settings
from app.ingestion import store
from app.models.config import UserConfig

router = APIRouter(prefix="/config", tags=["config"])


async def effective_config(session: AsyncSession, settings: Settings) -> UserConfig:
    """The persisted user config, or the env defaults when none has been saved yet."""
    return await store.load_user_config(session) or UserConfig.from_settings(settings)


@router.get("", response_model=UserConfig)
async def get_config(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> UserConfig:
    """The effective personal config (persisted row or env defaults)."""
    return await effective_config(session, settings)


@router.put("", response_model=UserConfig)
async def put_config(
    config: UserConfig,
    session: AsyncSession = Depends(get_session),
) -> UserConfig:
    """Persist the personal config (range-validated by the ``UserConfig`` model)."""
    await store.upsert_user_config(session, config)
    await session.commit()
    return config
