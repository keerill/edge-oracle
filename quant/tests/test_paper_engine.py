"""Paper-capture engine orchestration test — single cycle with fakes, no DB, no network.

Mirrors test_signals_engine: patch the store reads/writes so the engine runs entirely in
memory. Confirms the enrich -> select -> insert wiring captures a gate-passing directional
signal and dedups against an already-open paper trade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import Settings
from app.ingestion import store
from app.models.config import UserConfig
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import ExtremeCorrectionSignal
from app.paper import engine

AT = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def fake_sessionmaker():
    return FakeSession()


def _market() -> Market:
    return Market(
        market_id="m1",
        condition_id="c1",
        question="q",
        slug="s",
        category=None,
        event_id=None,
        yes_token_id="yes1",
        no_token_id="no1",
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("100"),
    )


def _yes_quote() -> QuoteSnapshot:
    # midpoint 0.40, spread 0.04 -> half_spread 0.02; threshold 0.44 < p_lo 0.50 -> gate passes
    return QuoteSnapshot(
        time=AT,
        token_id="yes1",
        market_id="m1",
        best_bid=Decimal("0.38"),
        best_bid_size=Decimal("100"),
        best_ask=Decimal("0.42"),
        best_ask_size=Decimal("100"),
        midpoint=Decimal("0.40"),
        spread=Decimal("0.04"),
    )


def _signal() -> ExtremeCorrectionSignal:
    # fair_value 0.55 > price 0.30 -> buy YES; p_side 0.55, p_lo 0.50 (margin 0.05)
    return ExtremeCorrectionSignal(
        time=AT, market_id="m1", condition_id="c1", price=Decimal("0.30"), fair_value=Decimal("0.55")
    )


@pytest.fixture
def patched(monkeypatch):
    """Patch every store read the engine performs + capture the write."""
    captured: dict = {}

    async def load_user_config(session):
        return UserConfig(
            bankroll=Decimal("1000"),
            kelly_frac=Decimal("0.25"),
            kelly_cap=Decimal("0.05"),
            corr_cap_frac=Decimal("0.05"),
            risk_threshold=Decimal("0.5"),
        )

    async def load_calibration(session, strategy=None):
        return []

    async def load_signals(session, *, strategy=None, limit=100):
        return [_signal()]

    async def load_tracked_markets(session):
        return [_market()]

    async def load_latest_quotes(session, *, token_ids=None):
        return {"yes1": _yes_quote()}

    async def load_paper_trades(session, *, status=None):
        return list(captured.get("open", []))

    async def insert_paper_trades(session, paper_trades):
        captured["written"] = list(paper_trades)
        return len(captured["written"])

    monkeypatch.setattr(store, "load_user_config", load_user_config)
    monkeypatch.setattr(store, "load_calibration", load_calibration)
    monkeypatch.setattr(store, "load_signals", load_signals)
    monkeypatch.setattr(store, "load_tracked_markets", load_tracked_markets)
    monkeypatch.setattr(store, "load_latest_quotes", load_latest_quotes)
    monkeypatch.setattr(store, "load_paper_trades", load_paper_trades)
    monkeypatch.setattr(store, "insert_paper_trades", insert_paper_trades)
    return captured


@pytest.mark.asyncio
async def test_captures_gate_passing_directional(patched):
    result = await engine.run_paper_capture_once(fake_sessionmaker, Settings())
    assert result.signals == 1
    assert result.captured == 1
    pt = patched["written"][0]
    assert pt.strategy == "extreme_correction"
    assert pt.side == "yes"
    assert pt.condition_id == "c1"
    assert pt.stake_usd > 0
    assert pt.p_lo == Decimal("0.50")


@pytest.mark.asyncio
async def test_dedups_against_open_paper_trade(patched):
    # an open paper trade for the same (strategy, condition) suppresses re-capture
    captured_pt = await engine.run_paper_capture_once(fake_sessionmaker, Settings())
    assert captured_pt.captured == 1
    patched["open"] = patched["written"]
    again = await engine.run_paper_capture_once(fake_sessionmaker, Settings())
    assert again.captured == 0
