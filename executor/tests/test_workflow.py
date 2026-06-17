"""Two-step propose → approve workflow (dry-run).

Offline tests monkeypatch the store into an in-memory fake to assert the audit trail and the two
safety properties end-to-end (propose stops at pending_approval; approve mints a hash-bound token,
signs, dry-run-submits). A DB-gated test runs the real persistence path through Postgres.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.config import Settings
from app.db import store
from app.models.advised import AdvisedSignalView, GateBreakdownView
from app.orchestrator.workflow import approve_and_sign, propose_signal
from app.signer.crypto import LocalSigner

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
SECRET = "approval-secret"
EXCHANGE = "0xExchange"


def _settings(**over) -> Settings:
    base = dict(
        signer_private_key=TEST_PK, approval_secret=SECRET, chain_id=137,
        allowlist_contracts=EXCHANGE, allowlist_spenders="",
        per_trade_cap_usd=Decimal("100"), max_slippage=Decimal("0.05"),
        approval_threshold_usd=Decimal("50"), hot_wallet_cap_usd=Decimal("500"),
        rate_limit_count=10, rate_limit_notional_usd=Decimal("1000"),
        dry_run=True, require_approval_for_all=True, intent_ttl_s=300, approval_token_ttl_s=300,
    )
    base.update(over)
    return Settings(**base)


def _advised(**over) -> AdvisedSignalView:
    base = dict(
        id="extreme_correction:m1:1", time=NOW, market_id="m1", condition_id="c1",
        strategy="extreme_correction", kind="buy_no", market_price=Decimal("0.18"),
        p=Decimal("0.70"), edge=Decimal("0.5"), net_edge=Decimal("0.06"),
        recommended_size_usd=Decimal("40"), recommended_size_pct=Decimal("0.04"),
        confidence=Decimal("0.5"), gate_passed=True,
        gate=GateBreakdownView(
            m=Decimal("0.18"), half_spread=Decimal("0.02"), slippage=Decimal("0.01"),
            gas=Decimal("0.01"), margin=Decimal("0.05"), p_lo=Decimal("0.65"),
            threshold=Decimal("0.22"),
        ),
    )
    base.update(over)
    return AdvisedSignalView(**base)


@pytest.fixture
def fake_store(monkeypatch):
    """In-memory store: capture the formed intent, the audit events, the approvals."""
    cap: dict = {"intent": None, "audit": [], "approvals": []}

    async def allocate_nonce(session, address, chain_id):
        return 5

    async def insert_intent(session, intent, intent_hash):
        cap["intent"] = intent

    async def load_intent(session, intent_id):
        return cap["intent"]

    async def append_audit(session, *, intent_id, event, detail=None, actor=None, tx_hash=None, time=None):
        cap["audit"].append(event)

    async def insert_approval(session, *, intent_id, approval_token_hash, threshold_usd, approver, granted_at, expires_at):
        cap["approvals"].append(approval_token_hash)

    monkeypatch.setattr(store, "allocate_nonce", allocate_nonce)
    monkeypatch.setattr(store, "insert_intent", insert_intent)
    monkeypatch.setattr(store, "load_intent", load_intent)
    monkeypatch.setattr(store, "append_audit", append_audit)
    monkeypatch.setattr(store, "insert_approval", insert_approval)
    return cap


async def test_propose_then_approve_dry_run(fake_store):
    settings = _settings()
    signer = LocalSigner(TEST_PK)

    proposed = await propose_signal(
        object(), _advised(), signer=signer, settings=settings, now=NOW, intent_id="i-1"
    )
    assert proposed.status == "pending_approval"
    assert fake_store["intent"] is not None  # persisted, awaiting approval
    assert fake_store["audit"] == ["formed", "pending_approval"]

    approved = await approve_and_sign(
        object(), "i-1", signer=signer, settings=settings, now=NOW
    )
    assert approved.status == "submitted"
    assert approved.submission is not None and approved.submission.status == "dry_run"
    assert approved.signed is not None and approved.signed.signer_address == TEST_ADDR
    assert len(fake_store["approvals"]) == 1  # only the token HASH is stored
    assert fake_store["audit"] == [
        "formed", "pending_approval", "approved", "signed", "submitted",
    ]


async def test_approve_missing_intent_is_not_found(fake_store):
    fake_store["intent"] = None  # load_intent returns None
    result = await approve_and_sign(
        object(), "nope", signer=LocalSigner(TEST_PK), settings=_settings(), now=NOW
    )
    assert result.status == "not_found"
    assert fake_store["audit"] == []  # nothing happened


# --- DB-gated end-to-end through real Postgres -------------------------------

TEST_DB = os.environ.get("EDGE_EXEC_TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def sessionmaker():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.tables import metadata

    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


@pytest.mark.skipif(TEST_DB is None, reason="EDGE_EXEC_TEST_DATABASE_URL not set")
async def test_propose_approve_persists_audit_trail(sessionmaker):
    settings = _settings()
    signer = LocalSigner(TEST_PK)
    async with sessionmaker() as s:
        proposed = await propose_signal(
            s, _advised(), signer=signer, settings=settings, now=NOW, intent_id="i-db"
        )
        await s.commit()
    assert proposed.status == "pending_approval"

    async with sessionmaker() as s:
        approved = await approve_and_sign(s, "i-db", signer=signer, settings=settings, now=NOW)
        await s.commit()
    assert approved.status == "submitted"
    assert approved.signed is not None and approved.signed.signer_address == TEST_ADDR

    async with sessionmaker() as s:
        trail = await store.load_audit_trail(s, "i-db")
    events = [r.event for r in trail]
    assert events == ["formed", "pending_approval", "approved", "signed", "submitted"]
