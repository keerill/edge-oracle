"""Backtest replay adapter — candidate construction (offline) + the DB-gated full run.

``build_candidates`` is pure over plain quotes/markets/resolutions, so it gets exact
offline tests. ``run_backtest_once`` is exercised against a throwaway DB (skipped unless
``EDGE_TEST_DATABASE_URL`` is set), seeding the same store the live poller writes to.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.backtest.engine import build_candidates, run_backtest_once
from app.config import Settings
from app.db.tables import metadata
from app.ingestion import store
from app.models.backtest import MarketResolution
from app.models.market import Market
from app.models.quote import QuoteSnapshot

T1 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
RESOLVE = datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc)


def _market(*, market_id="m1", condition_id="c1", yes="111", no="222", category="politics") -> Market:
    return Market(
        market_id=market_id,
        condition_id=condition_id,
        question="q",
        slug="s",
        category=category,
        event_id=None,
        yes_token_id=yes,
        no_token_id=no,
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("100"),
    )


def _quote(token, time, *, bid, ask, mid, size="100") -> QuoteSnapshot:
    b, a = Decimal(bid), Decimal(ask)
    return QuoteSnapshot(
        time=time,
        token_id=token,
        market_id="m1",
        best_bid=b,
        best_bid_size=Decimal(size),
        best_ask=a,
        best_ask_size=Decimal(size),
        midpoint=Decimal(mid),
        spread=a - b,
    )


# --------------------------------------------------------------------------- build_candidates


def test_build_candidates_low_extreme_emits_buy_yes_correction():
    quotes = [
        _quote("111", T1, bid="0.09", ask="0.11", mid="0.10"),  # YES extreme-low
        _quote("222", T1, bid="0.87", ask="0.89", mid="0.88"),  # NO complement (no arb)
    ]
    res = {"c1": MarketResolution(outcome=1, resolve_time=RESOLVE)}
    cands = build_candidates(quotes, [_market()], res)

    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "directional" and c.strategy == "extreme_correction"
    assert c.side == "yes"  # nudged up from 0.10 -> buy YES
    assert c.m_side == Decimal("0.10")
    assert c.p_yes > Decimal("0.10")  # fair_value above the price
    assert c.p_side == c.p_yes
    assert c.p_lo_side == c.p_yes - Decimal("0.05")  # default model-error margin
    assert c.half_spread == Decimal("0.01")  # spread 0.02 / 2
    assert c.entry_time == T1 and c.resolve_time == RESOLVE


def test_build_candidates_high_extreme_emits_buy_no_with_no_token_price():
    quotes = [
        _quote("111", T1, bid="0.89", ask="0.91", mid="0.90"),  # YES extreme-high
        _quote("222", T1, bid="0.07", ask="0.09", mid="0.08"),  # NO token
    ]
    res = {"c1": MarketResolution(outcome=0, resolve_time=RESOLVE)}
    cands = build_candidates(quotes, [_market()], res)

    assert len(cands) == 1
    c = cands[0]
    assert c.side == "no"  # nudged down from 0.90 -> buy NO
    assert c.m_side == Decimal("0.08")  # the NO token's own midpoint
    assert c.p_yes < Decimal("0.90")  # fair_value below the price
    assert c.p_side == Decimal("1") - c.p_yes


def test_build_candidates_detects_set_arb():
    quotes = [
        _quote("111", T1, bid="0.45", ask="0.46", mid="0.455"),  # not extreme -> no correction
        _quote("222", T1, bid="0.48", ask="0.49", mid="0.485"),
    ]
    res = {"c1": MarketResolution(outcome=0, resolve_time=RESOLVE)}
    cands = build_candidates(quotes, [_market()], res)

    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "arb" and c.strategy == "set_arb"
    # gross 1 - (0.46 + 0.49) = 0.05; costs 0.02 -> net 0.03; capital = 0.95 * 1 set.
    assert c.locked_net_edge == Decimal("0.03")
    assert c.set_size == Decimal("1")
    assert c.capital == Decimal("0.95")


def test_build_candidates_skips_markets_without_a_resolution():
    quotes = [
        _quote("111", T1, bid="0.09", ask="0.11", mid="0.10"),
        _quote("222", T1, bid="0.87", ask="0.89", mid="0.88"),
    ]
    assert build_candidates(quotes, [_market()], {}) == []


def test_build_candidates_enters_a_strategy_at_most_once_per_market():
    # Two extreme-low ticks; only the FIRST becomes a correction candidate.
    quotes = [
        _quote("111", T1, bid="0.09", ask="0.11", mid="0.10"),
        _quote("222", T1, bid="0.87", ask="0.89", mid="0.88"),
        _quote("111", T2, bid="0.08", ask="0.10", mid="0.09"),
        _quote("222", T2, bid="0.88", ask="0.90", mid="0.89"),
    ]
    res = {"c1": MarketResolution(outcome=1, resolve_time=RESOLVE)}
    cands = build_candidates(quotes, [_market()], res)
    assert len(cands) == 1
    assert cands[0].entry_time == T1  # the earliest qualifying tick


def test_build_candidates_no_signal_when_price_is_unremarkable():
    quotes = [
        _quote("111", T1, bid="0.49", ask="0.51", mid="0.50"),  # mid-range: no correction
        _quote("222", T1, bid="0.49", ask="0.51", mid="0.50"),  # asks sum 1.02: no arb
    ]
    res = {"c1": MarketResolution(outcome=1, resolve_time=RESOLVE)}
    assert build_candidates(quotes, [_market()], res) == []


# --------------------------------------------------------------------------- run_backtest_once (DB)

TEST_DB = os.environ.get("EDGE_TEST_DATABASE_URL")
db_only = pytest.mark.skipif(TEST_DB is None, reason="EDGE_TEST_DATABASE_URL not set")


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


@db_only
async def test_run_backtest_once_replays_a_seeded_arb_end_to_end(sessionmaker):
    # Seed one market + one tick with a clean long-set arb (net 0.03 over 1 set).
    quotes = [
        _quote("111", T1, bid="0.45", ask="0.46", mid="0.455"),
        _quote("222", T1, bid="0.48", ask="0.49", mid="0.485"),
    ]
    async with sessionmaker() as s:
        await store.upsert_markets(s, [_market()])
        await store.insert_quotes(s, quotes)
        await s.commit()

    resolutions = {"c1": MarketResolution(outcome=0, resolve_time=RESOLVE)}
    res = await run_backtest_once(sessionmaker, Settings(), resolutions)

    assert res.n_bets == 1
    assert res.initial_bankroll == Decimal("1000")
    assert res.final_bankroll == Decimal("1000.03")  # +0.03 locked edge, outcome-independent
    assert "set_arb" in res.per_strategy
    assert res.per_strategy["set_arb"].total_pnl == Decimal("0.03")
