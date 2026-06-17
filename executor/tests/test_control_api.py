"""Control API (Phase 6-UI) — pending list, detail+audit, approve. DB-gated, async ASGI client.

Skipped unless EDGE_EXEC_TEST_DATABASE_URL is set. Seeds a proposed (pending) intent, then drives
the real ASGI app (httpx ASGITransport, same event loop as the asyncpg engine) with the session +
settings dependencies overridden onto the test database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.main import _session, app
from app.config import Settings, get_settings
from app.db.tables import metadata
from app.models.advised import AdvisedSignalView, GateBreakdownView
from app.orchestrator.workflow import propose_signal
from app.signer.crypto import LocalSigner

TEST_DB = os.environ.get("EDGE_EXEC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_DB is None, reason="EDGE_EXEC_TEST_DATABASE_URL not set")

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXCHANGE = "0xExchange"


def _settings(**over) -> Settings:
    base = dict(
        signer_private_key=TEST_PK, approval_secret="s", chain_id=137,
        allowlist_contracts=EXCHANGE, per_trade_cap_usd=Decimal("100"),
        max_slippage=Decimal("0.05"), approval_threshold_usd=Decimal("50"),
        hot_wallet_cap_usd=Decimal("500"), rate_limit_count=10,
        rate_limit_notional_usd=Decimal("1000"), dry_run=True, require_approval_for_all=True,
    )
    base.update(over)
    return Settings(**base)


def _advised() -> AdvisedSignalView:
    return AdvisedSignalView(
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


@pytest_asyncio.fixture
async def client(request):
    over = getattr(request, "param", {})
    settings = _settings(**over)
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    # Seed at real wall-clock now so the intent isn't already expired when the API approves it
    # at datetime.now() (the signer rejects past-expiry intents — correct behavior).
    seed_now = datetime.now(tz=UTC)
    async with sessionmaker() as s:
        await propose_signal(
            s, _advised(), signer=LocalSigner(TEST_PK), settings=settings, now=seed_now,
            intent_id="i-1",
        )
        await s.commit()

    async def _override_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


async def test_pending_lists_the_proposed_intent(client):
    res = await client.get("/intents/pending")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["intent_id"] == "i-1"
    assert data[0]["side"] == "buy_no"
    assert data[0]["notional_usd"] == "40"  # Decimal-as-string


async def test_detail_includes_audit_trail(client):
    res = await client.get("/intents/i-1")
    assert res.status_code == 200
    body = res.json()
    assert body["intent"]["intent_id"] == "i-1"
    assert [e["event"] for e in body["audit"]] == ["formed", "pending_approval"]


async def test_approve_signs_and_clears_pending(client):
    res = await client.post("/intents/i-1/approve")
    assert res.status_code == 200
    assert res.json()["status"] == "submitted"
    assert (await client.get("/intents/pending")).json() == []


async def test_detail_404_for_unknown(client):
    assert (await client.get("/intents/nope")).status_code == 404


@pytest.mark.parametrize("client", [{"control_api_key": "secret"}], indirect=True)
async def test_api_key_required_when_configured(client):
    assert (await client.get("/intents/pending")).status_code == 401
    ok = await client.get("/intents/pending", headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
