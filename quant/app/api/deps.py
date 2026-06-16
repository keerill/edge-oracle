"""FastAPI dependencies — DB session/sessionmaker + settings.

Built on the process-wide factory in ``app.db.engine``; tests swap them via
``app.dependency_overrides`` to point at a throwaway database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker


def get_app_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The process-wide session factory (the backtest route opens its own session)."""
    return get_sessionmaker()


async def get_session() -> AsyncIterator[AsyncSession]:
    """A request-scoped read session (closed when the request ends)."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


def get_app_settings() -> Settings:
    """The cached settings singleton."""
    return get_settings()
