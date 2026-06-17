"""Store integration tests — DB-gated (skipped unless EDGE_EXEC_TEST_DATABASE_URL is set).

Targets store LOGIC and the money guards: Decimal<->NUMERIC exactness on the intent, the
append-only audit trail, the atomic nonce allocator (no duplicate nonce), allowlist round-trip,
and that only a token HASH is persisted (never a raw token).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import store
from app.db.tables import exec_approvals, exec_intents, metadata
from app.models.intent import Intent, compute_intent_hash

TEST_DB = os.environ.get("EDGE_EXEC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_DB is None, reason="EDGE_EXEC_TEST_DATABASE_URL not set")

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2026, 6, 17, 12, 5, 0, tzinfo=UTC)


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1", created_at=T0, expiry=EXP, source_signal_id="s1",
        action="clob_order", chain_id=137, market_id="m1", condition_id="c1",
        side="buy_no", size=Decimal("400.5"), max_price=Decimal("0.30"),
        max_slippage=Decimal("0.01"), notional_usd=Decimal("100.25"),
        to_address="0xExchange", token_id=None, approve_spender=None,
        approve_amount=None, nonce=0,
    )
    base.update(over)
    return Intent(**base)


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


async def test_intent_roundtrips_with_exact_decimals(sessionmaker):
    intent = _intent()
    h = compute_intent_hash(intent)
    async with sessionmaker() as s:
        await store.insert_intent(s, intent, h)
        await s.commit()
    async with sessionmaker() as s:
        row = (await s.execute(sa.select(exec_intents))).one()
    assert row.size == Decimal("400.5")
    assert row.notional_usd == Decimal("100.25")
    assert row.max_price == Decimal("0.30")
    assert row.intent_hash == h
    assert isinstance(row.size, Decimal)  # NUMERIC -> Decimal, never float


async def test_audit_trail_is_append_only_and_ordered(sessionmaker):
    async with sessionmaker() as s:
        await store.append_audit(s, intent_id="i-1", event="formed", actor="system", time=T0)
        await store.append_audit(s, intent_id="i-1", event="breaker_rejected",
                                 detail={"reason": "per-trade cap"}, time=EXP)
        await s.commit()
    async with sessionmaker() as s:
        trail = await store.load_audit_trail(s, "i-1")
    assert [r.event for r in trail] == ["formed", "breaker_rejected"]
    assert trail[1].detail == {"reason": "per-trade cap"}


async def test_nonce_allocator_is_monotonic_and_unique(sessionmaker):
    allocated = []
    async with sessionmaker() as s:
        for _ in range(3):
            allocated.append(await store.allocate_nonce(s, "0xWallet", 137))
        await s.commit()
    assert allocated == [0, 1, 2]
    # a different address has its own independent sequence
    async with sessionmaker() as s:
        other = await store.allocate_nonce(s, "0xOther", 137)
        await s.commit()
    assert other == 0


async def test_allowlist_roundtrip(sessionmaker):
    async with sessionmaker() as s:
        await store.add_allowlist_entry(s, address="0xExchange", kind="contract", label="CLOB")
        await store.add_allowlist_entry(s, address="0xCTF", kind="spender")
        await s.commit()
    async with sessionmaker() as s:
        contracts = await store.load_allowlist(s, "contract")
        spenders = await store.load_allowlist(s, "spender")
    assert contracts == frozenset({"0xExchange"})
    assert spenders == frozenset({"0xCTF"})


async def test_approval_stores_only_token_hash(sessionmaker):
    async with sessionmaker() as s:
        await store.insert_approval(
            s, intent_id="i-1", approval_token_hash="sha256:abc",
            threshold_usd=Decimal("50"), approver="alice@example.com",
            granted_at=T0, expires_at=EXP,
        )
        await s.commit()
    async with sessionmaker() as s:
        row = (await s.execute(sa.select(exec_approvals))).one()
    assert row.approval_token_hash == "sha256:abc"
    assert row.consumed is False
    # no column anywhere holds a raw token
    assert "token" not in {c.name for c in exec_approvals.c if c.name != "approval_token_hash"}
