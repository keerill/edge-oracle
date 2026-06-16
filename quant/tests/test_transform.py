"""Pure-transform tests — the money-correctness core. Sync, offline, deterministic.

Centerpiece: the worked numeric example for derived midpoint/spread, asserting the
results are exact ``Decimal`` values (never float). Also covers stringified-array
parsing, uint256-as-string token ids, empty/one-sided books, defensive best-level
selection, and universe ranking/selection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.ingestion.transform import (
    is_binary,
    market_from_raw,
    orderbook_from_raw,
    parse_stringified_str_array,
    quote_from_book,
    rank_and_select,
)
from app.models.book import BookLevel, OrderBook
from app.polymarket.schemas import RawGammaMarket, RawOrderBook

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


# --- parse_stringified_str_array ---------------------------------------------


def test_parse_stringified_array_forms():
    assert parse_stringified_str_array('["a", "b"]') == ["a", "b"]
    assert parse_stringified_str_array(["a", "b"]) == ["a", "b"]  # passthrough
    assert parse_stringified_str_array(None) == []
    assert parse_stringified_str_array("") == []


def test_parse_stringified_array_rejects_non_array_json():
    with pytest.raises(ValueError):
        parse_stringified_str_array("{}")  # a dict is not an array


# --- market_from_raw ----------------------------------------------------------


def test_market_from_raw_keeps_token_ids_as_strings(load_fixture):
    raw = RawGammaMarket.model_validate(load_fixture("gamma_markets.json")[0])
    market = market_from_raw(raw)

    assert (
        market.yes_token_id
        == "71321045679252212594626385532706912750332728571942532289631379312455583992563"
    )
    assert (
        market.no_token_id
        == "52833552901081050021711447516446057846099583932483975837835044438861178890963"
    )
    assert isinstance(market.yes_token_id, str)
    assert isinstance(market.no_token_id, str)
    # No precision loss: the huge id survives an int round-trip as a check.
    assert str(int(market.yes_token_id)) == market.yes_token_id
    assert market.outcomes == ("Yes", "No")
    assert market.event_id == "evt-9001"
    assert market.liquidity == Decimal("125000.5")
    assert isinstance(market.liquidity, Decimal)


def test_market_from_raw_parses_stringified_outcomes_and_missing_category(load_fixture):
    raw = RawGammaMarket.model_validate(load_fixture("gamma_markets.json")[3])  # fed-cut
    market = market_from_raw(raw)
    assert market.outcomes == ("Yes", "No")  # outcomes arrived stringified
    assert market.category is None
    assert market.liquidity == Decimal("42000.25")


def test_market_from_raw_rejects_non_two_token_market(load_fixture):
    raw = RawGammaMarket.model_validate(load_fixture("gamma_markets.json")[4])  # 1 token
    with pytest.raises(ValueError):
        market_from_raw(raw)


# --- is_binary ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["Yes", "No"], True),
        (["YES", "NO"], True),  # casefold tolerant
        (["yes", "no"], True),
        (["No", "Yes"], False),  # order-sensitive
        (["Team A", "Team B"], False),
        (["A", "B", "C"], False),
    ],
)
def test_is_binary(outcomes, expected):
    assert is_binary(outcomes) is expected


# --- quote_from_book: the worked numeric example ------------------------------


def test_quote_from_book_worked_example(load_fixture):
    # best bid 0.51, best ask 0.54  ->  midpoint 0.525, spread 0.03
    raw = RawOrderBook.model_validate(load_fixture("clob_book_full.json"))
    book = orderbook_from_raw(raw, token_id="713-yes")
    quote = quote_from_book(book, market_id="501234", at=AT)

    assert quote.best_bid == Decimal("0.51")
    assert quote.best_ask == Decimal("0.54")
    assert quote.best_bid_size == Decimal("1200")
    assert quote.best_ask_size == Decimal("900")
    assert quote.midpoint == Decimal("0.525")
    assert quote.spread == Decimal("0.03")
    # Types must be Decimal, never float.
    assert isinstance(quote.midpoint, Decimal)
    assert isinstance(quote.spread, Decimal)
    assert quote.time == AT
    assert quote.token_id == "713-yes"
    assert quote.market_id == "501234"


def test_quote_from_book_second_example():
    book = OrderBook(
        token_id="t",
        bids=(BookLevel(price=Decimal("0.49"), size=Decimal("10")),),
        asks=(BookLevel(price=Decimal("0.53"), size=Decimal("20")),),
    )
    quote = quote_from_book(book, market_id="m", at=AT)
    assert quote.midpoint == Decimal("0.51")
    assert quote.spread == Decimal("0.04")


def test_quote_from_book_empty_ask(load_fixture):
    raw = RawOrderBook.model_validate(load_fixture("clob_book_empty_ask.json"))
    book = orderbook_from_raw(raw, token_id="t")
    quote = quote_from_book(book, market_id="m", at=AT)
    assert quote.best_bid == Decimal("0.51")
    assert quote.best_bid_size == Decimal("1200")
    assert quote.best_ask is None
    assert quote.best_ask_size is None
    assert quote.midpoint is None
    assert quote.spread is None


def test_quote_from_book_empty_both(load_fixture):
    raw = RawOrderBook.model_validate(load_fixture("clob_book_empty_both.json"))
    book = orderbook_from_raw(raw, token_id="t")
    quote = quote_from_book(book, market_id="m", at=AT)
    assert quote.best_bid is None
    assert quote.best_ask is None
    assert quote.best_bid_size is None
    assert quote.best_ask_size is None
    assert quote.midpoint is None
    assert quote.spread is None


# --- defensive best-level selection ------------------------------------------


def test_best_levels_are_defensive_against_unsorted_input():
    raw = RawOrderBook.model_validate(
        {
            "bids": [
                {"price": "0.49", "size": "1"},
                {"price": "0.51", "size": "2"},  # best bid (highest), not first
                {"price": "0.50", "size": "3"},
            ],
            "asks": [
                {"price": "0.56", "size": "1"},
                {"price": "0.54", "size": "2"},  # best ask (lowest), not first
                {"price": "0.55", "size": "3"},
            ],
        }
    )
    book = orderbook_from_raw(raw, token_id="t")
    assert book.best_bid.price == Decimal("0.51")
    assert book.best_bid.size == Decimal("2")
    assert book.best_ask.price == Decimal("0.54")
    assert book.best_ask.size == Decimal("2")


# --- rank_and_select ----------------------------------------------------------


def _markets(load_fixture):
    out = []
    for m in load_fixture("gamma_markets.json"):
        raw = RawGammaMarket.model_validate(m)
        try:
            out.append(market_from_raw(raw))
        except ValueError:
            pass  # the single-token market (501238) is skipped here
    return out


def test_rank_and_select_filters_and_orders(load_fixture):
    selected = rank_and_select(_markets(load_fixture), top_n=50)
    ids = [m.market_id for m in selected]
    # Kept: 501234 (politics, high liq) and 501237 (fed-cut, lower liq).
    # Dropped: 501235 (not Yes/No), 501236 (closed), 501239 (order book disabled).
    assert ids == ["501234", "501237"]
    # ranked by liquidity desc
    assert selected[0].liquidity > selected[1].liquidity


def test_rank_and_select_respects_top_n(load_fixture):
    selected = rank_and_select(_markets(load_fixture), top_n=1)
    assert [m.market_id for m in selected] == ["501234"]


def test_rank_and_select_allowlist_overrides_binary_filter(load_fixture):
    markets = _markets(load_fixture)
    team_market = next(m for m in markets if m.market_id == "501235")  # Team A/B
    selected = rank_and_select(markets, top_n=50, allowlist=[team_market.condition_id])
    # Allowlist override keeps the non-Yes/No market (it is active/open/orderbook).
    assert [m.market_id for m in selected] == ["501235"]


def test_rank_and_select_allowlist_still_requires_snapshotable(load_fixture):
    markets = _markets(load_fixture)
    closed_market = next(m for m in markets if m.market_id == "501236")  # closed
    selected = rank_and_select(markets, top_n=50, allowlist=[closed_market.condition_id])
    assert selected == []  # cannot snapshot a closed market even if allowlisted
