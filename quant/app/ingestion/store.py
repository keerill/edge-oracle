"""Async writes — the only DB-write module. Sessions are caller-owned.

``Decimal`` passes straight through to the NUMERIC columns (asyncpg maps it
natively); ``QuoteSnapshot.model_dump()`` preserves ``Decimal``/``datetime`` (python
mode), so no float ever reaches the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import calibration as calibration_table
from app.db.tables import markets as markets_table
from app.db.tables import quotes as quotes_table
from app.db.tables import signals as signals_table
from app.models.calibration import CalibrationRecord
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import Signal

# Market columns updated on conflict (everything except the PK and created_at).
_MARKET_UPDATE_COLS = (
    "condition_id",
    "question",
    "slug",
    "category",
    "event_id",
    "yes_token_id",
    "no_token_id",
    "enable_order_book",
    "active",
    "closed",
    "liquidity",
)


def _market_row(m: Market) -> dict:
    return {
        "market_id": m.market_id,
        "condition_id": m.condition_id,
        "question": m.question,
        "slug": m.slug,
        "category": m.category,
        "event_id": m.event_id,
        "yes_token_id": m.yes_token_id,
        "no_token_id": m.no_token_id,
        "enable_order_book": m.enable_order_book,
        "active": m.active,
        "closed": m.closed,
        "tracked": True,
        "liquidity": m.liquidity,
    }


async def upsert_markets(session: AsyncSession, markets: Sequence[Market]) -> None:
    """Insert/update the tracked universe (``tracked=True`` for all given)."""
    if not markets:
        return
    stmt = pg_insert(markets_table).values([_market_row(m) for m in markets])
    set_ = {col: getattr(stmt.excluded, col) for col in _MARKET_UPDATE_COLS}
    set_["tracked"] = True
    set_["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=["market_id"], set_=set_)
    await session.execute(stmt)


async def set_untracked(session: AsyncSession, keep_ids: set[str]) -> None:
    """Flip ``tracked=False`` for currently-tracked markets not in ``keep_ids``."""
    stmt = update(markets_table).where(markets_table.c.tracked.is_(True))
    if keep_ids:
        stmt = stmt.where(markets_table.c.market_id.notin_(keep_ids))
    await session.execute(stmt.values(tracked=False, updated_at=func.now()))


async def insert_quotes(session: AsyncSession, quotes: Sequence[QuoteSnapshot]) -> int:
    """Append a tick's worth of quote snapshots in a single batch insert."""
    if not quotes:
        return 0
    rows = [q.model_dump() for q in quotes]
    await session.execute(insert(quotes_table), rows)
    return len(rows)


async def load_tracked_markets(session: AsyncSession) -> list[Market]:
    """Reload the currently-tracked universe from the DB (no ``outcomes`` persisted).

    The read counterpart to ``upsert_markets`` — consumed by the signal engine to scan
    over the stored universe.
    """
    rows = (
        await session.execute(
            select(markets_table).where(markets_table.c.tracked.is_(True))
        )
    ).mappings().all()
    return [
        Market(
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            question=r["question"],
            slug=r["slug"],
            category=r["category"],
            event_id=r["event_id"],
            yes_token_id=r["yes_token_id"],
            no_token_id=r["no_token_id"],
            enable_order_book=r["enable_order_book"],
            active=r["active"],
            closed=r["closed"],
            liquidity=r["liquidity"],
        )
        for r in rows
    ]


async def load_quotes(
    session: AsyncSession,
    *,
    token_ids: Sequence[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[QuoteSnapshot]:
    """Reload stored top-of-book snapshots **time-ordered** (then by token) — the read
    counterpart to ``insert_quotes``, consumed by the backtest replay.

    Optional ``token_ids`` restricts to specific tokens; ``start``/``end`` bound the
    window half-open ``[start, end)``. ``Decimal`` comes straight back from NUMERIC.
    """
    stmt = select(quotes_table)
    if token_ids is not None:
        stmt = stmt.where(quotes_table.c.token_id.in_(list(token_ids)))
    if start is not None:
        stmt = stmt.where(quotes_table.c.time >= start)
    if end is not None:
        stmt = stmt.where(quotes_table.c.time < end)
    rows = (
        await session.execute(stmt.order_by(quotes_table.c.time, quotes_table.c.token_id))
    ).mappings().all()
    return [
        QuoteSnapshot(
            time=r["time"],
            token_id=r["token_id"],
            market_id=r["market_id"],
            best_bid=r["best_bid"],
            best_bid_size=r["best_bid_size"],
            best_ask=r["best_ask"],
            best_ask_size=r["best_ask_size"],
            midpoint=r["midpoint"],
            spread=r["spread"],
        )
        for r in rows
    ]


async def insert_signals(session: AsyncSession, signals: Sequence[Signal]) -> int:
    """Append detected signals (any strategy) in a single batch insert.

    **One call must be homogeneous** — a single strategy/model type: the executemany
    compiles its column list from the first row's ``model_dump()`` keys, so mixing shapes
    in one call would drop columns. Each scanner persists its own strategy's batch.
    """
    if not signals:
        return 0
    rows = [s.model_dump() for s in signals]
    await session.execute(insert(signals_table), rows)
    return len(rows)


async def insert_calibration(
    session: AsyncSession, records: Sequence[CalibrationRecord]
) -> int:
    """Append resolved-prediction records to the calibration journal (single batch)."""
    if not records:
        return 0
    rows = [r.model_dump() for r in records]
    await session.execute(insert(calibration_table), rows)
    return len(rows)


async def load_calibration(
    session: AsyncSession, strategy: str | None = None
) -> list[CalibrationRecord]:
    """Reload the calibration journal (optionally one strategy), oldest first — the read
    counterpart to ``insert_calibration``, consumed by the calibration scoring."""
    stmt = select(calibration_table)
    if strategy is not None:
        stmt = stmt.where(calibration_table.c.strategy == strategy)
    rows = (
        await session.execute(stmt.order_by(calibration_table.c.time))
    ).mappings().all()
    return [
        CalibrationRecord(
            time=r["time"],
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            strategy=r["strategy"],
            estimate=r["estimate"],
            price=r["price"],
            outcome=r["outcome"],
        )
        for r in rows
    ]
