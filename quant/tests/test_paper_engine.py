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
from app.models.signal import ArbSignal, ExtremeCorrectionSignal
from app.paper import engine
from app.polymarket.schemas import RawOrderBook

AT = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
CHECKED = datetime(2026, 6, 17, 12, 0, 8, tzinfo=UTC)  # 8s after AT


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


# --- set-arb fill re-check ----------------------------------------------------


def _arb_market() -> Market:
    return Market(
        market_id="m2",
        condition_id="c2",
        question="q",
        slug="s",
        category=None,
        event_id=None,
        yes_token_id="y2",
        no_token_id="n2",
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("100"),
    )


def _arb_signal(net_edge=Decimal("0.04")) -> ArbSignal:
    # a stale detection-time net_edge of 0.04; the fresh book decides the real edge
    return ArbSignal(
        time=AT,
        market_id="m2",
        condition_id="c2",
        kind="long_set",
        yes_price=Decimal("0.46"),
        no_price=Decimal("0.49"),
        set_size=Decimal("1"),
        gross_edge=Decimal("0.05"),
        estimated_costs=Decimal("0.02"),
        net_edge=net_edge,
        hypothetical_pnl=net_edge,
    )


def _raw_book(bids, asks) -> RawOrderBook:
    return RawOrderBook.model_validate(
        {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        }
    )


class FakeClob:
    def __init__(self, books, errors=()):
        self._books = books
        self._errors = set(errors)

    async def get_book(self, token_id):
        if token_id in self._errors:
            raise RuntimeError(f"boom: {token_id}")
        return self._books[token_id]


@pytest.fixture
def patched_arb(monkeypatch):
    """Patch store reads so the engine enriches exactly one set-arb signal; capture the write."""
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
        return [_arb_signal()]

    async def load_tracked_markets(session):
        return [_arb_market()]

    async def load_latest_quotes(session, *, token_ids=None):
        return {}

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
async def test_arb_survives_fill_check_captures_verified(patched_arb):
    # fresh book still arbs: asks 0.46 + 0.49 = 0.95 -> net 0.03 after 0.02 costs
    clob = FakeClob({"y2": _raw_book([], [("0.46", "5")]), "n2": _raw_book([], [("0.49", "5")])})
    result = await engine.run_paper_capture_once(
        fake_sessionmaker, Settings(), clob=clob, now=lambda: CHECKED
    )
    assert result.captured == 1 and result.arb_verified == 1 and result.arb_expired == 0
    pt = patched_arb["written"][0]
    assert pt.side == "set" and pt.status == "open"
    assert pt.fill_ok is True and pt.fill_reason == "ok"
    assert pt.rechecked_net_edge == Decimal("0.03")  # the verified edge, not the stale 0.04
    assert pt.fill_latency_s == Decimal("8")


@pytest.mark.asyncio
async def test_arb_edge_collapsed_captures_expired(patched_arb):
    # fresh book no longer arbs: asks 0.50 + 0.51 = 1.01 -> no edge
    clob = FakeClob({"y2": _raw_book([], [("0.50", "5")]), "n2": _raw_book([], [("0.51", "5")])})
    result = await engine.run_paper_capture_once(
        fake_sessionmaker, Settings(), clob=clob, now=lambda: CHECKED
    )
    assert result.captured == 1 and result.arb_verified == 0 and result.arb_expired == 1
    pt = patched_arb["written"][0]
    assert pt.status == "expired" and pt.fill_ok is False
    assert pt.fill_reason == "edge_collapsed" and pt.realized_pnl is None


@pytest.mark.asyncio
async def test_arb_missing_leg_is_skipped(patched_arb):
    clob = FakeClob({"n2": _raw_book([], [("0.49", "5")])}, errors={"y2"})
    result = await engine.run_paper_capture_once(
        fake_sessionmaker, Settings(), clob=clob, now=lambda: CHECKED
    )
    assert result.captured == 0  # not inserted; key stays free to retry next cycle
    assert patched_arb["written"] == []


@pytest.mark.asyncio
async def test_arb_fill_check_disabled_captures_optimistically(patched_arb):
    # check off -> no CLOB read, legacy fill-optimistic capture (open, fill_ok None)
    result = await engine.run_paper_capture_once(
        fake_sessionmaker, Settings(arb_fill_check_enabled=False), now=lambda: CHECKED
    )
    assert result.captured == 1 and result.arb_verified == 0
    pt = patched_arb["written"][0]
    assert pt.status == "open" and pt.fill_ok is None and pt.rechecked_net_edge is None
