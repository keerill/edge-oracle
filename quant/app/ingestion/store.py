"""Async writes — the only DB-write module. Sessions are caller-owned.

``Decimal`` passes straight through to the NUMERIC columns (asyncpg maps it
natively); ``QuoteSnapshot.model_dump()`` preserves ``Decimal``/``datetime`` (python
mode), so no float ever reaches the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import calibration as calibration_table
from app.db.tables import markets as markets_table
from app.db.tables import paper_trades as paper_trades_table
from app.db.tables import positions as positions_table
from app.db.tables import quotes as quotes_table
from app.db.tables import signals as signals_table
from app.db.tables import trades as trades_table
from app.db.tables import user_config as user_config_table
from app.models.calibration import CalibrationRecord
from app.models.config import UserConfig
from app.models.market import Market
from app.models.paper_trade import PaperTrade
from app.models.position import Position
from app.models.quote import QuoteSnapshot
from app.models.signal import (
    ArbSignal,
    ExtremeCorrectionSignal,
    FavouriteLongshotSignal,
    Signal,
)
from app.models.trade import Trade

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


async def insert_trades(session: AsyncSession, trades: Sequence[Trade]) -> int:
    """Append trade prints in a single batch insert (``Decimal`` straight to NUMERIC)."""
    if not trades:
        return 0
    rows = [t.model_dump() for t in trades]
    await session.execute(insert(trades_table), rows)
    return len(rows)


async def load_trades(
    session: AsyncSession,
    *,
    token_ids: Sequence[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Trade]:
    """Reload stored trade prints **time-ordered** (then by token) — the read counterpart to
    ``insert_trades``. Optional ``token_ids`` filter; ``start``/``end`` bound the window
    half-open ``[start, end)``. ``Decimal`` comes straight back from NUMERIC."""
    stmt = select(trades_table)
    if token_ids is not None:
        stmt = stmt.where(trades_table.c.token_id.in_(list(token_ids)))
    if start is not None:
        stmt = stmt.where(trades_table.c.time >= start)
    if end is not None:
        stmt = stmt.where(trades_table.c.time < end)
    rows = (
        await session.execute(stmt.order_by(trades_table.c.time, trades_table.c.token_id))
    ).mappings().all()
    return [
        Trade(
            time=r["time"],
            token_id=r["token_id"],
            market_id=r["market_id"],
            price=r["price"],
            size=r["size"],
            taker_side=r["taker_side"],
            trade_id=r["trade_id"],
        )
        for r in rows
    ]


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


async def load_latest_quotes(
    session: AsyncSession, *, token_ids: Sequence[str] | None = None
) -> dict[str, QuoteSnapshot]:
    """Return the **newest** snapshot per token (``token_id -> QuoteSnapshot``) — what the
    advisor needs to price each live signal. ``DISTINCT ON (token_id)`` keeps one row per token
    (the freshest), served straight off ``ix_quotes_token_time`` ``(token_id, time DESC)``.
    """
    stmt = select(quotes_table)
    if token_ids is not None:
        stmt = stmt.where(quotes_table.c.token_id.in_(list(token_ids)))
    stmt = stmt.distinct(quotes_table.c.token_id).order_by(
        quotes_table.c.token_id, quotes_table.c.time.desc()
    )
    rows = (await session.execute(stmt)).mappings().all()
    return {
        r["token_id"]: QuoteSnapshot(
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
    }


def _signal_from_row(r) -> Signal:
    """Rebuild the concrete ``Signal`` subclass from a ``signals`` row by its ``strategy`` tag
    (each strategy populated only its own nullable columns)."""
    strategy = r["strategy"]
    if strategy == "set_arb":
        return ArbSignal(
            time=r["time"],
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            kind=r["kind"],
            yes_price=r["yes_price"],
            no_price=r["no_price"],
            set_size=r["set_size"],
            gross_edge=r["gross_edge"],
            estimated_costs=r["estimated_costs"],
            net_edge=r["net_edge"],
            hypothetical_pnl=r["hypothetical_pnl"],
        )
    if strategy == "favourite_longshot":
        return FavouriteLongshotSignal(
            time=r["time"],
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            kind=r["kind"],
            price=r["price"],
            edge_score=r["edge_score"],
        )
    if strategy == "extreme_correction":
        return ExtremeCorrectionSignal(
            time=r["time"],
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            price=r["price"],
            fair_value=r["fair_value"],
        )
    raise ValueError(f"unknown signal strategy: {strategy!r}")


async def load_signals(
    session: AsyncSession, *, strategy: str | None = None, limit: int = 100
) -> list[Signal]:
    """Reload recent detected signals, **newest first** — the read counterpart to
    ``insert_signals`` consumed by the advisor REST layer. Optional ``strategy`` filter; capped
    by ``limit``. ``Decimal`` comes straight back from NUMERIC (no float)."""
    stmt = select(signals_table)
    if strategy is not None:
        stmt = stmt.where(signals_table.c.strategy == strategy)
    stmt = stmt.order_by(signals_table.c.time.desc()).limit(limit)
    rows = (await session.execute(stmt)).mappings().all()
    return [_signal_from_row(r) for r in rows]


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


_USER_CONFIG_ID = "default"
_USER_CONFIG_COLS = ("bankroll", "kelly_frac", "kelly_cap", "corr_cap_frac", "risk_threshold")


async def load_user_config(session: AsyncSession) -> UserConfig | None:
    """Load the single persisted user config, or ``None`` if none has been saved yet (the
    caller then falls back to ``UserConfig.from_settings``)."""
    row = (
        await session.execute(
            select(user_config_table).where(user_config_table.c.id == _USER_CONFIG_ID)
        )
    ).mappings().first()
    if row is None:
        return None
    return UserConfig(
        bankroll=row["bankroll"],
        kelly_frac=row["kelly_frac"],
        kelly_cap=row["kelly_cap"],
        corr_cap_frac=row["corr_cap_frac"],
        risk_threshold=row["risk_threshold"],
    )


async def upsert_user_config(session: AsyncSession, config: UserConfig) -> None:
    """Persist the user config to the single 'default' row (insert or update in place)."""
    values = {"id": _USER_CONFIG_ID, **{c: getattr(config, c) for c in _USER_CONFIG_COLS}}
    stmt = pg_insert(user_config_table).values(values)
    set_ = {c: getattr(stmt.excluded, c) for c in _USER_CONFIG_COLS}
    set_["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=set_)
    await session.execute(stmt)


def _position_from_row(r) -> Position:
    return Position(
        id=r["id"],
        created_at=r["created_at"],
        market_id=r["market_id"],
        condition_id=r["condition_id"],
        strategy=r["strategy"],
        side=r["side"],
        entry_price=r["entry_price"],
        stake_usd=r["stake_usd"],
        shares=r["shares"],
        status=r["status"],
        outcome=r["outcome"],
        pnl=r["pnl"],
        resolved_at=r["resolved_at"],
        signal_id=r["signal_id"],
    )


async def insert_position(session: AsyncSession, position: Position) -> None:
    """Record a placed bet (the server has stamped id/created_at/shares)."""
    await session.execute(insert(positions_table).values(position.model_dump()))


async def load_positions(
    session: AsyncSession, *, status: str | None = None
) -> list[Position]:
    """Reload portfolio positions, newest first. Optional ``status`` filter (open/closed)."""
    stmt = select(positions_table)
    if status is not None:
        stmt = stmt.where(positions_table.c.status == status)
    rows = (
        await session.execute(stmt.order_by(positions_table.c.created_at.desc()))
    ).mappings().all()
    return [_position_from_row(r) for r in rows]


async def settle_position(
    session: AsyncSession,
    position_id: str,
    *,
    outcome: int,
    pnl: Decimal,
    resolved_at: datetime,
) -> None:
    """Close a resolved position: record its realized outcome + P&L (idempotent re-runs are
    avoided by the caller, which only settles ``status='open'`` rows)."""
    await session.execute(
        update(positions_table)
        .where(positions_table.c.id == position_id)
        .values(status="closed", outcome=outcome, pnl=pnl, resolved_at=resolved_at)
    )


def _paper_trade_from_row(r) -> PaperTrade:
    return PaperTrade(
        id=r["id"],
        advised_at=r["advised_at"],
        strategy=r["strategy"],
        market_id=r["market_id"],
        condition_id=r["condition_id"],
        side=r["side"],
        advised_price=r["advised_price"],
        stake_usd=r["stake_usd"],
        shares=r["shares"],
        edge=r["edge"],
        p=r["p"],
        p_lo=r["p_lo"],
        status=r["status"],
        outcome=r["outcome"],
        realized_pnl=r["realized_pnl"],
        resolved_at=r["resolved_at"],
        signal_id=r["signal_id"],
        fill_checked_at=r["fill_checked_at"],
        fill_ok=r["fill_ok"],
        fill_latency_s=r["fill_latency_s"],
        fill_reason=r["fill_reason"],
        rechecked_net_edge=r["rechecked_net_edge"],
    )


async def insert_paper_trades(
    session: AsyncSession, paper_trades: Sequence[PaperTrade]
) -> int:
    """Append auto-captured advisory recommendations. Idempotent on ``id`` (ON CONFLICT DO
    NOTHING guards the rare PK reuse when an old signal re-fires at the same epoch_ms after its
    ``(strategy, condition_id)`` key frees up). Returns the rows actually inserted — skipped
    duplicates don't count (and never raise, so they can't kill a capture cycle)."""
    if not paper_trades:
        return 0
    stmt = (
        pg_insert(paper_trades_table)
        .values([pt.model_dump() for pt in paper_trades])
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(paper_trades_table.c.id)
    )
    result = await session.execute(stmt)
    return len(result.fetchall())


async def load_paper_trades(
    session: AsyncSession, *, status: str | None = None
) -> list[PaperTrade]:
    """Reload paper trades, newest first. Optional ``status`` filter (open/closed/expired)."""
    stmt = select(paper_trades_table)
    if status is not None:
        stmt = stmt.where(paper_trades_table.c.status == status)
    rows = (
        (await session.execute(stmt.order_by(paper_trades_table.c.advised_at.desc())))
        .mappings()
        .all()
    )
    return [_paper_trade_from_row(r) for r in rows]


async def settle_paper_trade(
    session: AsyncSession,
    paper_trade_id: str,
    *,
    outcome: int | None,
    realized_pnl: Decimal,
    resolved_at: datetime,
    status: str = "closed",
) -> None:
    """Close a paper trade with its realized P&L (idempotent: the caller only settles
    ``status='open'`` rows). ``outcome`` is the market's YES result for directional rows, or
    ``None`` for outcome-independent set-arb."""
    await session.execute(
        update(paper_trades_table)
        .where(paper_trades_table.c.id == paper_trade_id)
        .values(status=status, outcome=outcome, realized_pnl=realized_pnl, resolved_at=resolved_at)
    )


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
