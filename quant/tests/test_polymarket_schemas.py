"""Boundary-validation tests: raw schemas accept real wire shapes and reject junk.

These are pure/offline (no asyncio, no network). They lock the untrusted-input gate:
Polymarket responses are validated structurally, prices/ids stay strings, unknown
fields are ignored, and structurally-broken payloads raise.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.polymarket.schemas import (
    RawGammaMarket,
    RawMidpoint,
    RawOrderBook,
    RawPricesHistory,
    RawSpread,
)


def test_gamma_market_accepts_binary_market_and_keeps_strings(load_fixture):
    markets = load_fixture("gamma_markets.json")
    a = RawGammaMarket.model_validate(markets[0])  # the high-liquidity politics market

    assert a.id == "501234"
    assert isinstance(a.id, str)
    assert a.conditionId == "0x00000000000000000000000000000000000000000000000000000000000000aa"
    # clobTokenIds stays a STRINGIFIED json array at the boundary (parsed later).
    assert isinstance(a.clobTokenIds, str)
    assert a.clobTokenIds.startswith("[")
    assert a.category == "politics"
    assert a.enableOrderBook is True
    assert a.active is True
    assert a.closed is False


def test_gamma_market_unknown_fields_ignored(load_fixture):
    markets = load_fixture("gamma_markets.json")
    # market[0] carries an 'extraUnknownField' that must be silently dropped.
    a = RawGammaMarket.model_validate(markets[0])
    assert not hasattr(a, "extraUnknownField")


def test_gamma_market_missing_category_defaults_none(load_fixture):
    markets = load_fixture("gamma_markets.json")
    d = RawGammaMarket.model_validate(markets[3])  # 'fed-cut-rates' has no category key
    assert d.category is None
    # outcomes can arrive stringified — accepted as a str here, normalized downstream.
    assert isinstance(d.outcomes, str)


def test_gamma_market_id_coerced_from_number():
    # Gamma occasionally sends ``id`` as a JSON number; it must become a string.
    m = RawGammaMarket.model_validate(
        {"id": 12345, "question": "q", "slug": "s", "conditionId": "0xabc"}
    )
    assert m.id == "12345"
    assert isinstance(m.id, str)


def test_gamma_market_rejects_malformed_missing_condition_id(load_fixture):
    bad = load_fixture("gamma_market_malformed.json")
    with pytest.raises(ValidationError):
        RawGammaMarket.model_validate(bad)


def test_order_book_accepts_full_and_keeps_string_prices(load_fixture):
    book = RawOrderBook.model_validate(load_fixture("clob_book_full.json"))
    assert len(book.bids) == 3
    assert len(book.asks) == 3
    # prices/sizes are strings at the boundary (Decimal coercion happens in transform).
    assert isinstance(book.bids[0].price, str)
    assert isinstance(book.bids[0].size, str)
    assert book.bids[0].price == "0.51"
    assert book.asks[0].price == "0.54"
    assert book.asset_id.startswith("71321045679252")


def test_order_book_accepts_empty_sides(load_fixture):
    empty_ask = RawOrderBook.model_validate(load_fixture("clob_book_empty_ask.json"))
    assert len(empty_ask.bids) == 1
    assert empty_ask.asks == []

    empty_both = RawOrderBook.model_validate(load_fixture("clob_book_empty_both.json"))
    assert empty_both.bids == []
    assert empty_both.asks == []


def test_order_book_rejects_level_missing_size(load_fixture):
    bad = load_fixture("clob_book_malformed.json")  # a bid level has no 'size'
    with pytest.raises(ValidationError):
        RawOrderBook.model_validate(bad)


def test_midpoint_and_spread_parse(load_fixture):
    mid = RawMidpoint.model_validate(load_fixture("clob_midpoint.json"))
    assert mid.midpoint == "0.525"

    spread = RawSpread.model_validate(load_fixture("clob_spread.json"))
    assert spread.spread == "0.03"
    assert spread.bid == "0.51"
    assert spread.ask == "0.54"


def test_prices_history_types(load_fixture):
    hist = RawPricesHistory.model_validate(load_fixture("clob_prices_history.json"))
    assert len(hist.history) == 3
    point = hist.history[0]
    assert isinstance(point.t, int)  # unix seconds
    assert isinstance(point.p, float)  # the only legitimate float; never stored
    assert point.t == 1718500000
    assert point.p == 0.61
