"""Store integration tests — DB-gated.

Skipped unless ``EDGE_TEST_DATABASE_URL`` points at a throwaway Postgres/TimescaleDB.
The schema is created from ``tables.py`` metadata (plain tables — the hypertable is
exercised separately by ``alembic upgrade head``); these tests target store LOGIC and
the critical NUMERIC<->Decimal money guard.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.tables import markets as markets_table
from app.db.tables import metadata
from app.db.tables import quotes as quotes_table
from app.db.tables import signals as signals_table
from app.ingestion import store
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import ArbSignal

TEST_DB = os.environ.get("EDGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_DB is None, reason="EDGE_TEST_DATABASE_URL not set")


def _market(*, market_id="m1", condition_id="c1", question="q", liquidity=Decimal("100")) -> Market:
    return Market(
        market_id=market_id,
        condition_id=condition_id,
        question=question,
        slug="slug",
        category="politics",
        event_id=None,
        outcomes=("Yes", "No"),
        yes_token_id="111",
        no_token_id="222",
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=liquidity,
    )


def _reset_schema(sync_conn) -> None:
    metadata.drop_all(sync_conn)
    metadata.create_all(sync_conn)


@pytest_asyncio.fixture
async def sessionmaker():
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(_reset_schema)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


async def test_upsert_markets_is_idempotent(sessionmaker):
    async with sessionmaker() as s:
        await store.upsert_markets(s, [_market(question="original")])
        await s.commit()
    async with sessionmaker() as s:
        await store.upsert_markets(s, [_market(question="updated")])
        await s.commit()

    async with sessionmaker() as s:
        rows = (await s.execute(sa.select(markets_table))).mappings().all()
    assert len(rows) == 1
    assert rows[0]["question"] == "updated"  # conflict updated the row in place
    assert rows[0]["tracked"] is True


async def test_insert_quotes_decimal_roundtrip(sessionmaker):
    quote = QuoteSnapshot(
        time=datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        token_id="111",
        market_id="m1",
        best_bid=Decimal("0.51"),
        best_bid_size=Decimal("1200"),
        best_ask=Decimal("0.54"),
        best_ask_size=Decimal("900"),
        midpoint=Decimal("0.525"),
        spread=Decimal("0.03"),
    )
    async with sessionmaker() as s:
        n = await store.insert_quotes(s, [quote])
        await s.commit()
    assert n == 1

    async with sessionmaker() as s:
        row = (await s.execute(sa.select(quotes_table))).mappings().one()
    # The money guard: values come back as Decimal, exactly, never float.
    assert isinstance(row["midpoint"], Decimal)
    assert row["midpoint"] == Decimal("0.525")
    assert row["spread"] == Decimal("0.03")
    assert row["best_bid"] == Decimal("0.51")
    assert row["best_ask_size"] == Decimal("900")


async def test_insert_quotes_allows_null_sides(sessionmaker):
    quote = QuoteSnapshot(
        time=datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        token_id="111",
        market_id="m1",
        best_bid=Decimal("0.51"),
        best_bid_size=Decimal("1200"),
        best_ask=None,
        best_ask_size=None,
        midpoint=None,
        spread=None,
    )
    async with sessionmaker() as s:
        await store.insert_quotes(s, [quote])
        await s.commit()
    async with sessionmaker() as s:
        row = (await s.execute(sa.select(quotes_table))).mappings().one()
    assert row["best_ask"] is None
    assert row["midpoint"] is None


async def test_set_untracked(sessionmaker):
    async with sessionmaker() as s:
        await store.upsert_markets(
            s, [_market(market_id="m1", condition_id="c1"), _market(market_id="m2", condition_id="c2")]
        )
        await s.commit()
    async with sessionmaker() as s:
        await store.set_untracked(s, {"m1"})
        await s.commit()

    async with sessionmaker() as s:
        rows = {
            r["market_id"]: r["tracked"]
            for r in (await s.execute(sa.select(markets_table))).mappings()
        }
    assert rows["m1"] is True
    assert rows["m2"] is False


async def test_insert_signals_decimal_roundtrip(sessionmaker):
    sig = ArbSignal(
        time=datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        market_id="m1",
        condition_id="c1",
        kind="long_set",
        yes_price=Decimal("0.46"),
        no_price=Decimal("0.49"),
        set_size=Decimal("1"),
        gross_edge=Decimal("0.05"),
        estimated_costs=Decimal("0.02"),
        net_edge=Decimal("0.03"),
        hypothetical_pnl=Decimal("0.03"),
    )
    async with sessionmaker() as s:
        n = await store.insert_signals(s, [sig])
        await s.commit()
    assert n == 1

    async with sessionmaker() as s:
        row = (await s.execute(sa.select(signals_table))).mappings().one()
    # The money guard: edges come back as Decimal, exactly, never float.
    assert isinstance(row["net_edge"], Decimal)
    assert row["gross_edge"] == Decimal("0.05")
    assert row["estimated_costs"] == Decimal("0.02")
    assert row["net_edge"] == Decimal("0.03")
    assert row["hypothetical_pnl"] == Decimal("0.03")
    assert row["yes_price"] == Decimal("0.46")
    assert row["kind"] == "long_set"


async def test_load_tracked_markets_returns_only_tracked(sessionmaker):
    async with sessionmaker() as s:
        await store.upsert_markets(
            s, [_market(market_id="m1", condition_id="c1"), _market(market_id="m2", condition_id="c2")]
        )
        await s.commit()
    async with sessionmaker() as s:
        await store.set_untracked(s, {"m1"})  # keep only m1 tracked
        await s.commit()

    async with sessionmaker() as s:
        loaded = await store.load_tracked_markets(s)

    assert [m.market_id for m in loaded] == ["m1"]
    assert isinstance(loaded[0], Market)
    assert loaded[0].condition_id == "c1"
    assert loaded[0].yes_token_id == "111"
    assert loaded[0].no_token_id == "222"
