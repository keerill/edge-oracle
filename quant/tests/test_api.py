"""API integration tests — DB-gated.

Skipped unless ``EDGE_TEST_DATABASE_URL`` points at a throwaway Postgres/TimescaleDB. Seeds
markets + quotes + signals + calibration, then drives the real ASGI app through httpx with the
session/sessionmaker/settings dependencies overridden onto the test database. Asserts the
response *shape* (incl. the deliberate Decimal->JSON-string money contract) and the one
worked-number sizing anchor, so the live endpoint and the pure ``advise`` math stay in sync.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_app_sessionmaker, get_app_settings, get_session
from app.config import Settings
from app.db.tables import metadata
from app.ingestion import store
from app.main import app
from app.models.calibration import CalibrationRecord
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import ArbSignal, ExtremeCorrectionSignal, FavouriteLongshotSignal

TEST_DB = os.environ.get("EDGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_DB is None, reason="EDGE_TEST_DATABASE_URL not set")

T = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _reset_schema(sync_conn) -> None:
    metadata.drop_all(sync_conn)
    metadata.create_all(sync_conn)


def _market() -> Market:
    return Market(
        market_id="m1",
        condition_id="c1",
        question="Will it?",
        slug="will-it",
        category="politics",
        event_id=None,
        yes_token_id="111",
        no_token_id="222",
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("1000"),
    )


def _quote(token_id: str, mid: str) -> QuoteSnapshot:
    half = Decimal("0.02")  # spread 0.04
    return QuoteSnapshot(
        time=T,
        token_id=token_id,
        market_id="m1",
        best_bid=Decimal(mid) - half,
        best_bid_size=Decimal("100"),
        best_ask=Decimal(mid) + half,
        best_ask_size=Decimal("100"),
        midpoint=Decimal(mid),
        spread=Decimal("0.04"),
    )


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(_reset_schema)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    # Seed: one market, both tokens' latest quotes, one signal per strategy, two calib records.
    async with sessionmaker() as s:
        await store.upsert_markets(s, [_market()])
        await store.insert_quotes(s, [_quote("111", "0.40"), _quote("222", "0.60")])
        await store.insert_signals(
            s,
            [
                ExtremeCorrectionSignal(
                    time=T, market_id="m1", condition_id="c1",
                    price=Decimal("0.40"), fair_value=Decimal("0.55"),
                )
            ],
        )
        await store.insert_signals(
            s,
            [
                ArbSignal(
                    time=datetime(2026, 6, 16, 12, 1, tzinfo=UTC),
                    market_id="m1", condition_id="c1", kind="long_set",
                    yes_price=Decimal("0.46"), no_price=Decimal("0.49"), set_size=Decimal("1"),
                    gross_edge=Decimal("0.05"), estimated_costs=Decimal("0.02"),
                    net_edge=Decimal("0.03"), hypothetical_pnl=Decimal("0.03"),
                )
            ],
        )
        await store.insert_signals(
            s,
            [
                FavouriteLongshotSignal(
                    time=datetime(2026, 6, 16, 12, 2, tzinfo=UTC),
                    market_id="m1", condition_id="c1", kind="buy_no",
                    price=Decimal("0.10"), edge_score=Decimal("0.5"),
                )
            ],
        )
        await store.insert_calibration(
            s,
            [
                CalibrationRecord(time=T, market_id="m1", condition_id="c1",
                                  strategy="extreme_correction", estimate=Decimal("0.90"),
                                  price=Decimal("0.85"), outcome=1),
                CalibrationRecord(time=T, market_id="m1", condition_id="c1",
                                  strategy="extreme_correction", estimate=Decimal("0.20"),
                                  price=Decimal("0.25"), outcome=0),
            ],
        )
        await s.commit()

    async def _override_session():
        async with sessionmaker() as session:
            yield session

    test_settings = Settings(
        backtest_initial_bankroll=Decimal("1000"),
        kelly_frac=Decimal("0.25"),
        kelly_cap=Decimal("0.05"),
        arb_slippage=Decimal("0.01"),
        arb_gas=Decimal("0.01"),
        model_error_margin=Decimal("0.05"),
        backtest_resolutions_path=None,
    )
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_app_sessionmaker] = lambda: sessionmaker
    app.dependency_overrides[get_app_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
        await engine.dispose()


async def test_list_signals_shape_and_sort(client):
    resp = await client.get("/signals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    # Sorted by net_edge desc: correction (0.06) > arb (0.03) > longshot (0, display-only).
    assert [d["strategy"] for d in data] == [
        "extreme_correction",
        "set_arb",
        "favourite_longshot",
    ]
    # Money crosses the wire as JSON STRINGS (Decimal), never floats.
    correction = next(d for d in data if d["strategy"] == "extreme_correction")
    assert isinstance(correction["recommended_size_usd"], str)
    assert isinstance(correction["net_edge"], str)
    # Worked anchor: cap binds -> 1000 * 0.05 = 50.00; gate passes; net_edge = 0.50 - 0.44.
    assert Decimal(correction["recommended_size_usd"]) == Decimal("50.00")
    assert correction["gate_passed"] is True
    assert correction["market_question"] == "Will it?"  # joined from the Market
    assert correction["p"] == "0.55"
    assert Decimal(correction["net_edge"]) == Decimal("0.06")
    assert correction["gate"]["threshold"] == "0.44"


async def test_get_signal_detail_and_404(client):
    listing = (await client.get("/signals")).json()
    correction = next(d for d in listing if d["strategy"] == "extreme_correction")
    detail = await client.get(f"/signals/{correction['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == correction["id"]
    assert body["gate"]["p_lo"] == "0.50"
    assert Decimal(body["recommended_size_usd"]) == Decimal("50.00")

    missing = await client.get("/signals/extreme_correction:m1:9999")
    assert missing.status_code == 404


async def test_calibration_summary(client):
    resp = await client.get("/calibration")
    assert resp.status_code == 200
    body = resp.json()
    assert body is not None
    assert body["overall"]["n"] == 2
    # The cumulative timeline rides along: both records share a timestamp -> one pooled point.
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["n"] == 2


async def test_config_defaults_then_persists_and_resizes(client):
    # No row yet -> effective config is the env defaults.
    cfg = (await client.get("/config")).json()
    assert Decimal(cfg["bankroll"]) == Decimal("1000")
    assert Decimal(cfg["kelly_frac"]) == Decimal("0.25")
    assert Decimal(cfg["risk_threshold"]) == Decimal("1")

    # Persist a bigger bankroll; the cap (5%) now binds at 2000 * 0.05 = 100.00.
    put = await client.put(
        "/config",
        json={
            "bankroll": "2000",
            "kelly_frac": "0.25",
            "kelly_cap": "0.05",
            "corr_cap_frac": "0.05",
            "risk_threshold": "0.5",
        },
    )
    assert put.status_code == 200
    assert (await client.get("/config")).json()["bankroll"] == "2000"

    listing = (await client.get("/signals")).json()
    correction = next(d for d in listing if d["strategy"] == "extreme_correction")
    assert Decimal(correction["recommended_size_usd"]) == Decimal("100.00")  # 2000 * 0.05


async def test_config_rejects_out_of_range(client):
    bad = await client.put(
        "/config",
        json={
            "bankroll": "1000",
            "kelly_frac": "1.5",  # > 1 -> rejected
            "kelly_cap": "0.05",
            "corr_cap_frac": "0.05",
            "risk_threshold": "0.5",
        },
    )
    assert bad.status_code == 422


async def test_economics_surfaced_on_signals(client):
    listing = (await client.get("/signals")).json()
    correction = next(d for d in listing if d["strategy"] == "extreme_correction")
    econ = correction["economics"]
    assert econ is not None
    assert econ["ask"] == "0.44"  # all-in threshold
    assert isinstance(econ["ev_usd"], str)  # Decimal-as-string contract
    assert econ["prob_of_loss"] == "0.45"  # 1 - 0.55
    arb = next(d for d in listing if d["strategy"] == "set_arb")
    assert Decimal(arb["economics"]["locked_profit_usd"]) == Decimal("0.03")
    assert arb["economics"]["prob_of_loss"] == "0"


async def test_safe_only_sort_puts_arb_first(client):
    data = (await client.get("/signals?sort=safety&safe_only=true")).json()
    # longshot (tier 2) dropped; arb (tier 0) first, then gate-passing correction (tier 1).
    assert [d["strategy"] for d in data] == ["set_arb", "extreme_correction"]


async def test_backtest_zero_bets_without_resolutions(client):
    resp = await client.get("/backtest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_bets"] == 0
    assert Decimal(body["initial_bankroll"]) == Decimal("1000")
    # No bets -> the resampled distribution is undefined and serializes as null.
    assert body["monte_carlo"] is None
