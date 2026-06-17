"""Trade-ingestion orchestration tests — single cycle with fakes, no DB, no network.

Covers: mapping prints to their market, dropping prints for foreign tokens, per-market
isolation (a failing market is skipped), and the ``since`` cursor de-duplicating across cycles.
The pure transform is covered in test_trades_transform.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.ingestion import store, trades_engine
from app.models.market import Market
from app.polymarket.schemas import RawTrade


def _market(market_id, condition_id, yes_token, no_token) -> Market:
    return Market(
        market_id=market_id, condition_id=condition_id, question="q", slug="s",
        category=None, event_id=None, yes_token_id=yes_token, no_token_id=no_token,
        enable_order_book=True, active=True, closed=False, liquidity=None,
    )


def _raw(asset, *, ts, tid, cond="c", price="0.50", size="10", side="BUY") -> RawTrade:
    return RawTrade(
        asset=asset, conditionId=cond, side=side, size=size, price=price,
        timestamp=ts, transactionHash=tid,
    )


class FakeData:
    """Returns preloaded raw trades by condition id; conditions in ``errors`` raise."""

    def __init__(self, by_condition, errors=()):
        self._by_condition = by_condition
        self._errors = set(errors)
        self.calls: list[str] = []

    async def get_trades(self, *, condition_id=None, limit=None):
        self.calls.append(condition_id)
        if condition_id in self._errors:
            raise RuntimeError("boom")
        return list(self._by_condition.get(condition_id, []))


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
def capture_trades(monkeypatch):
    captured: dict = {}

    async def fake_insert(session, trades):
        captured.setdefault("trades", []).extend(trades)
        return len(list(trades))

    monkeypatch.setattr(store, "insert_trades", fake_insert)
    return captured


def _patch_universe(monkeypatch, markets):
    async def fake_load(session):
        return list(markets)

    monkeypatch.setattr(store, "load_tracked_markets", fake_load)


async def test_maps_prints_and_drops_foreign_tokens(monkeypatch, capture_trades):
    m = _market("mA", "cA", "A-yes", "A-no")
    _patch_universe(monkeypatch, [m])
    data = FakeData({
        "cA": [
            _raw("A-yes", ts=100, tid="t1", cond="cA"),
            _raw("A-no", ts=101, tid="t2", cond="cA"),
            _raw("SOMETHING-ELSE", ts=102, tid="t3", cond="cA"),  # not our token -> dropped
        ]
    })
    result = await trades_engine.run_trades_scan_once(data, fake_sessionmaker, Settings())
    trades = capture_trades["trades"]
    assert {t.trade_id for t in trades} == {"t1", "t2"}
    assert all(t.market_id == "mA" for t in trades)
    assert result.trades == 2


async def test_per_market_isolation(monkeypatch, capture_trades):
    good = _market("mA", "cA", "A-yes", "A-no")
    bad = _market("mBad", "cBad", "B-yes", "B-no")
    _patch_universe(monkeypatch, [good, bad])
    data = FakeData({"cA": [_raw("A-yes", ts=100, tid="t1", cond="cA")]}, errors={"cBad"})
    result = await trades_engine.run_trades_scan_once(data, fake_sessionmaker, Settings())
    assert [t.trade_id for t in capture_trades["trades"]] == ["t1"]
    assert result.markets == 2 and result.trades == 1


async def test_since_cursor_dedups_across_cycles(monkeypatch, capture_trades):
    m = _market("mA", "cA", "A-yes", "A-no")
    _patch_universe(monkeypatch, [m])
    t_old = _raw("A-yes", ts=100, tid="t1", cond="cA")
    t_new = _raw("A-yes", ts=200, tid="t2", cond="cA")
    since: dict[str, datetime] = {}

    data1 = FakeData({"cA": [t_old]})
    await trades_engine.run_trades_scan_once(data1, fake_sessionmaker, Settings(), since=since)
    assert since["A-yes"] == datetime.fromtimestamp(100, tz=timezone.utc)

    # next cycle re-returns the old trade plus a newer one; only the newer is inserted
    data2 = FakeData({"cA": [t_old, t_new]})
    await trades_engine.run_trades_scan_once(data2, fake_sessionmaker, Settings(), since=since)
    assert [t.trade_id for t in capture_trades["trades"]] == ["t1", "t2"]
    assert since["A-yes"] == datetime.fromtimestamp(200, tz=timezone.utc)
