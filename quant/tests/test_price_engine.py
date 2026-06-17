"""Price-signal scanner orchestration — single cycle with fakes, no DB/network.

Covers: reading the latest YES midpoint, both heuristics firing on a deep longshot, a mid-range
price producing nothing, a favourite producing only a longshot, and a missing midpoint skipped.
The pure band/nudge math is covered in test_longshot.py / test_correction.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.ingestion import store
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.signals import price_engine

AT = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _market(mid) -> Market:
    return Market(
        market_id=mid, condition_id="c" + mid, question="q", slug="s", category=None,
        event_id=None, yes_token_id="y" + mid, no_token_id="n" + mid,
        enable_order_book=True, active=True, closed=False, liquidity=Decimal("1"),
    )


def _quote(token, midpoint) -> QuoteSnapshot:
    return QuoteSnapshot(
        time=AT, token_id=token, market_id="m", best_bid=None, best_bid_size=None,
        best_ask=None, best_ask_size=None,
        midpoint=(Decimal(midpoint) if midpoint is not None else None), spread=None,
    )


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def fake_sessionmaker():
    return FakeSession()


def _patch(monkeypatch, markets, quotes_by_token, captured):
    async def load_markets(session):
        return list(markets)

    async def load_latest(session, *, token_ids=None):
        return dict(quotes_by_token)

    async def insert_signals(session, signals):
        sigs = list(signals)
        for s in sigs:
            captured.setdefault(s.strategy, []).append(s)
        return len(sigs)

    monkeypatch.setattr(store, "load_tracked_markets", load_markets)
    monkeypatch.setattr(store, "load_latest_quotes", load_latest)
    monkeypatch.setattr(store, "insert_signals", insert_signals)


async def test_deep_longshot_fires_both_heuristics(monkeypatch):
    captured: dict = {}
    m = _market("m1")
    _patch(monkeypatch, [m], {"ym1": _quote("ym1", "0.10")}, captured)
    result = await price_engine.run_price_scan_once(fake_sessionmaker, Settings(), now=lambda: AT)
    assert result.corrections == 1 and result.longshots == 1
    assert captured["extreme_correction"][0].market_id == "m1"
    assert captured["favourite_longshot"][0].kind == "buy_no"


async def test_mid_range_price_emits_nothing(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, [_market("m1")], {"ym1": _quote("ym1", "0.50")}, captured)
    result = await price_engine.run_price_scan_once(fake_sessionmaker, Settings())
    assert result.corrections == 0 and result.longshots == 0
    assert captured == {}


async def test_favourite_emits_only_longshot(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, [_market("m1")], {"ym1": _quote("ym1", "0.80")}, captured)
    result = await price_engine.run_price_scan_once(fake_sessionmaker, Settings())
    assert result.longshots == 1 and result.corrections == 0
    assert captured["favourite_longshot"][0].kind == "buy_yes"


async def test_missing_midpoint_is_skipped(monkeypatch):
    captured: dict = {}
    _patch(monkeypatch, [_market("m1")], {"ym1": _quote("ym1", None)}, captured)
    result = await price_engine.run_price_scan_once(fake_sessionmaker, Settings())
    assert result.markets == 1 and result.corrections == 0 and result.longshots == 0
