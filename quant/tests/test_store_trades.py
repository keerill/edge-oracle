"""Store integration test for trade prints — DB-gated (EDGE_TEST_DATABASE_URL).

Targets the trades round-trip and the money guard: Decimal<->NUMERIC exactness, time-ordering,
the token filter, and the half-open [start, end) window — mirroring the quotes store tests.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.tables import metadata
from app.ingestion import store
from app.models.trade import Trade

TEST_DB = os.environ.get("EDGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_DB is None, reason="EDGE_TEST_DATABASE_URL not set")


def _trade(*, t, token="111", price="0.81", size="30.864194", side="BUY", tid="0xabc") -> Trade:
    return Trade(
        time=t, token_id=token, market_id="m1",
        price=Decimal(price), size=Decimal(size), taker_side=side, trade_id=tid,
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


async def test_trades_roundtrip_exact_decimals_and_order(sessionmaker):
    t1 = datetime(2026, 6, 17, 7, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 17, 7, 5, 0, tzinfo=timezone.utc)
    async with sessionmaker() as s:
        # insert out of order; load must come back time-ordered
        n = await store.insert_trades(s, [_trade(t=t2, tid="0x2"), _trade(t=t1, tid="0x1")])
        await s.commit()
    assert n == 2
    async with sessionmaker() as s:
        got = await store.load_trades(s)
    assert [t.trade_id for t in got] == ["0x1", "0x2"]
    assert got[0].price == Decimal("0.81")
    assert got[0].size == Decimal("30.864194")  # exact, NUMERIC<->Decimal
    assert isinstance(got[0].price, Decimal)
    assert got[0].taker_side == "BUY"


async def test_load_trades_token_filter_and_window(sessionmaker):
    t1 = datetime(2026, 6, 17, 7, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 17, 7, 5, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 6, 17, 7, 10, 0, tzinfo=timezone.utc)
    async with sessionmaker() as s:
        await store.insert_trades(s, [
            _trade(t=t1, token="111", tid="a"),
            _trade(t=t2, token="222", tid="b"),
            _trade(t=t3, token="111", tid="c"),
        ])
        await s.commit()
    async with sessionmaker() as s:
        only_111 = await store.load_trades(s, token_ids=["111"])
        windowed = await store.load_trades(s, start=t1, end=t3)  # half-open: excludes t3
    assert [t.trade_id for t in only_111] == ["a", "c"]
    assert [t.trade_id for t in windowed] == ["a", "b"]
