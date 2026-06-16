"""Scanner orchestration tests — single cycle with fakes, no DB, no network.

Covers: discovery filtering + upsert, two get_book calls per market, derived Decimal
mid/spread on the persisted snapshots, per-token untrusted-rejection isolation, the
empty-book tick still recorded, the non-discovery (reload-from-DB) path, and the
two-cadence discovery timing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import Settings
from app.ingestion import scanner, store, transform
from app.polymarket.schemas import RawGammaMarket, RawOrderBook

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


# --- fakes --------------------------------------------------------------------


class FakeGamma:
    def __init__(self, raw_markets):
        self._raw_markets = raw_markets

    async def list_active_markets(self, **kwargs):
        return self._raw_markets


class FakeClob:
    """Returns preloaded books by token id; tokens in ``errors`` simulate a
    malformed response rejected at the boundary (ValidationError)."""

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


class FakeClock:
    def __init__(self, start, step):
        self.t = start
        self.step = step

    def __call__(self):
        cur = self.t
        self.t = self.t + self.step
        return cur


@pytest.fixture
def capture_store(monkeypatch):
    """Patch the store write functions to capture args instead of hitting a DB."""
    captured: dict = {}

    async def fake_upsert(session, markets):
        captured["markets"] = list(markets)

    async def fake_untracked(session, keep_ids):
        captured["untracked"] = set(keep_ids)

    async def fake_insert(session, quotes):
        captured["quotes"] = list(quotes)
        return len(captured["quotes"])

    monkeypatch.setattr(store, "upsert_markets", fake_upsert)
    monkeypatch.setattr(store, "set_untracked", fake_untracked)
    monkeypatch.setattr(store, "insert_quotes", fake_insert)
    return captured


# --- tests --------------------------------------------------------------------


async def test_run_scan_once_discovers_filters_and_snapshots(load_fixture, capture_store):
    raw_markets = [RawGammaMarket.model_validate(m) for m in load_fixture("gamma_markets.json")]
    # Derive the two tracked markets (501234, 501237) the same way the scanner does.
    discovered = []
    for r in raw_markets:
        try:
            discovered.append(transform.market_from_raw(r))
        except ValueError:
            pass
    selected = transform.rank_and_select(discovered, top_n=50)
    a = next(m for m in selected if m.market_id == "501234")
    d = next(m for m in selected if m.market_id == "501237")

    full = RawOrderBook.model_validate(load_fixture("clob_book_full.json"))
    empty = RawOrderBook.model_validate(load_fixture("clob_book_empty_both.json"))
    books = {a.yes_token_id: full, a.no_token_id: full, d.no_token_id: empty}
    clob = FakeClob(books, errors={d.yes_token_id})  # one token's book is malformed

    result = await scanner.run_scan_once(
        FakeGamma(raw_markets), clob, fake_sessionmaker, Settings(),
        do_discovery=True, now=lambda: AT,
    )

    # Discovery upserted exactly the two binary/open markets, untracking the rest.
    assert {m.market_id for m in capture_store["markets"]} == {"501234", "501237"}
    assert capture_store["untracked"] == {"501234", "501237"}
    # token ids preserved as strings (uint256)
    assert isinstance(a.yes_token_id, str)

    # Both tokens of both markets were attempted (4 get_book calls).
    assert set(clob.calls) == {a.yes_token_id, a.no_token_id, d.yes_token_id, d.no_token_id}

    quotes = capture_store["quotes"]
    by_token = {q.token_id: q for q in quotes}
    # d.yes was malformed -> skipped; the other three recorded.
    assert set(by_token) == {a.yes_token_id, a.no_token_id, d.no_token_id}
    assert result.markets == 2
    assert result.quotes == 3
    assert result.discovered is True

    # derived Decimal mid/spread on a full book; injected capture time
    a_yes = by_token[a.yes_token_id]
    assert a_yes.midpoint == Decimal("0.525")
    assert a_yes.spread == Decimal("0.03")
    assert isinstance(a_yes.midpoint, Decimal)
    assert a_yes.time == AT
    # empty book still recorded, with None prices
    assert by_token[d.no_token_id].midpoint is None
    assert by_token[d.no_token_id].best_bid is None


async def test_run_scan_once_without_discovery_reloads_tracked(load_fixture, capture_store, monkeypatch):
    raw_a = RawGammaMarket.model_validate(load_fixture("gamma_markets.json")[0])
    a = transform.market_from_raw(raw_a)

    async def fake_load_tracked(session):
        return [a]

    monkeypatch.setattr(scanner, "_load_tracked", fake_load_tracked)

    full = RawOrderBook.model_validate(load_fixture("clob_book_full.json"))
    clob = FakeClob({a.yes_token_id: full, a.no_token_id: full})

    result = await scanner.run_scan_once(
        FakeGamma([]), clob, fake_sessionmaker, Settings(),
        do_discovery=False, now=lambda: AT,
    )

    assert "markets" not in capture_store  # no discovery -> no upsert
    assert result.discovered is False
    assert result.markets == 1
    assert result.quotes == 2


async def test_run_poller_discovery_cadence(monkeypatch):
    calls: list[bool] = []

    async def fake_scan(gamma, clob, sm, settings, *, do_discovery, now):
        calls.append(do_discovery)
        return scanner.ScanResult(markets=0, quotes=0, discovered=do_discovery)

    monkeypatch.setattr(scanner, "run_scan_once", fake_scan)

    settings = Settings(scan_interval_s=15.0, discovery_interval_s=20.0)
    clock = FakeClock(start=AT, step=timedelta(seconds=15))

    async def noop_sleep(_delay):
        return None

    await scanner.run_poller(
        None, None, None, settings, now=clock, sleep=noop_sleep, max_cycles=3
    )
    # discover at cycle 0; skip at 15s (<20s); discover again at 30s (>=20s)
    assert calls == [True, False, True]
