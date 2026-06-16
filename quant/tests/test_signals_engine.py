"""Signal-engine orchestration tests — single cycle with fakes, no DB, no network.

Covers: scanning the stored universe, flagging exactly the arbing market, a non-arb
market producing nothing, per-market isolation (a bad book skips just that market),
and the injected capture time. The pure arb math is covered in test_arb.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.config import Settings
from app.ingestion import store
from app.models.market import Market
from app.polymarket.schemas import RawOrderBook
from app.signals import engine

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


# --- fakes --------------------------------------------------------------------


def _market(market_id: str, condition_id: str, yes_token: str, no_token: str) -> Market:
    return Market(
        market_id=market_id,
        condition_id=condition_id,
        question="q",
        slug="s",
        category=None,
        event_id=None,
        yes_token_id=yes_token,
        no_token_id=no_token,
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("100"),
    )


def _raw_book(bids, asks) -> RawOrderBook:
    return RawOrderBook.model_validate(
        {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        }
    )


class FakeClob:
    """Returns preloaded books by token id; tokens in ``errors`` simulate a malformed
    response rejected at the boundary (ValidationError)."""

    def __init__(self, books, errors=()):
        self._books = books
        self._errors = set(errors)
        self.calls: list[str] = []

    async def get_book(self, token_id):
        self.calls.append(token_id)
        if token_id in self._errors:
            RawOrderBook.model_validate({"bids": [{"price": "0.5"}], "asks": []})  # raises
        return self._books[token_id]


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def fake_sessionmaker():
    return FakeSession()


@pytest.fixture
def capture_signals(monkeypatch):
    """Patch the signal write to capture args instead of hitting a DB."""
    captured: dict = {}

    async def fake_insert(session, signals):
        captured["signals"] = list(signals)
        return len(captured["signals"])

    monkeypatch.setattr(store, "insert_signals", fake_insert)
    return captured


def _patch_universe(monkeypatch, markets):
    async def fake_load(session):
        return list(markets)

    monkeypatch.setattr(store, "load_tracked_markets", fake_load)


# --- tests --------------------------------------------------------------------


async def test_run_signal_scan_once_flags_only_the_arbing_market(monkeypatch, capture_signals):
    a = _market("mA", "cA", "A-yes", "A-no")  # asks 0.46 + 0.49 = 0.95 -> long arb
    b = _market("mB", "cB", "B-yes", "B-no")  # asks 0.51 + 0.51 = 1.02 -> no arb
    _patch_universe(monkeypatch, [a, b])
    books = {
        "A-yes": _raw_book([("0.40", "5")], [("0.46", "5")]),
        "A-no": _raw_book([("0.40", "5")], [("0.49", "5")]),
        "B-yes": _raw_book([("0.49", "5")], [("0.51", "5")]),
        "B-no": _raw_book([("0.49", "5")], [("0.51", "5")]),
    }
    clob = FakeClob(books)

    result = await engine.run_signal_scan_once(
        clob, fake_sessionmaker, Settings(), now=lambda: AT
    )

    signals = capture_signals["signals"]
    assert len(signals) == 1
    sig = signals[0]
    assert sig.market_id == "mA"
    assert sig.condition_id == "cA"
    assert sig.kind == "long_set"
    assert sig.net_edge == Decimal("0.03")  # 5c gross - 2c default costs
    assert sig.time == AT  # injected capture time
    assert result.markets == 2
    assert result.signals == 1
    # Both tokens of both markets were fetched.
    assert set(clob.calls) == {"A-yes", "A-no", "B-yes", "B-no"}


async def test_run_signal_scan_once_per_market_isolation(monkeypatch, capture_signals):
    a = _market("mA", "cA", "A-yes", "A-no")  # arbing
    bad = _market("mBad", "cBad", "Bad-yes", "Bad-no")  # YES book is malformed
    _patch_universe(monkeypatch, [a, bad])
    books = {
        "A-yes": _raw_book([("0.40", "5")], [("0.46", "5")]),
        "A-no": _raw_book([("0.40", "5")], [("0.49", "5")]),
        "Bad-no": _raw_book([("0.40", "5")], [("0.49", "5")]),
    }
    clob = FakeClob(books, errors={"Bad-yes"})

    result = await engine.run_signal_scan_once(
        clob, fake_sessionmaker, Settings(), now=lambda: AT
    )

    # The bad market can't be evaluated (missing a leg) but never kills the cycle.
    assert [s.market_id for s in capture_signals["signals"]] == ["mA"]
    assert result.markets == 2
    assert result.signals == 1


async def test_run_signal_scan_once_no_opportunities(monkeypatch, capture_signals):
    b = _market("mB", "cB", "B-yes", "B-no")
    _patch_universe(monkeypatch, [b])
    books = {
        "B-yes": _raw_book([("0.49", "5")], [("0.51", "5")]),
        "B-no": _raw_book([("0.49", "5")], [("0.51", "5")]),
    }

    result = await engine.run_signal_scan_once(
        FakeClob(books), fake_sessionmaker, Settings(), now=lambda: AT
    )

    assert capture_signals["signals"] == []
    assert result.markets == 1
    assert result.signals == 0
