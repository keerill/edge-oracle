"""Set-arbitrage math tests — the money-correctness core. Sync, offline, deterministic.

Order books are built inline as hand-computed fixtures so every number can be checked
by hand. All results must be exact ``Decimal`` (never float). Covers VWAP-to-fill over
depth, the LONG/SHORT set-arb worked examples, the cost-as-threshold gate, and the
no-arb / insufficient-depth rejections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.math.arb import (
    ArbParams,
    Fill,
    evaluate_long_set,
    evaluate_market,
    evaluate_short_set,
    vwap_to_fill,
)
from app.models.book import BookLevel, OrderBook

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)


def _levels(*pairs: tuple[str, str]) -> tuple[BookLevel, ...]:
    """BookLevels from (price, size) string pairs, in the order given."""
    return tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in pairs)


def _book(*, bids=(), asks=(), token_id="t") -> OrderBook:
    return OrderBook(token_id=token_id, bids=bids, asks=asks)


# --- vwap_to_fill -------------------------------------------------------------


def test_vwap_single_level_with_enough_size():
    # Top level alone covers the 1-share fill -> VWAP is just the top price.
    fill = vwap_to_fill(_levels(("0.46", "5")), Decimal("1"))
    assert fill == Fill(avg_price=Decimal("0.46"), filled_qty=Decimal("1"), fully_filled=True)
    assert isinstance(fill.avg_price, Decimal)


def test_vwap_walks_multiple_levels():
    # Fill 1 share across two levels: 0.4 @ 0.46 + 0.6 @ 0.47
    #   = 0.184 + 0.282 = 0.466 cost over 1.0 share -> VWAP 0.466
    fill = vwap_to_fill(_levels(("0.46", "0.4"), ("0.47", "1.0")), Decimal("1"))
    assert fill.avg_price == Decimal("0.466")
    assert fill.filled_qty == Decimal("1")
    assert fill.fully_filled is True


def test_vwap_exact_level_boundary():
    # Two levels sum to exactly the requested quantity.
    fill = vwap_to_fill(_levels(("0.46", "0.4"), ("0.47", "0.6")), Decimal("1"))
    assert fill.avg_price == Decimal("0.466")
    assert fill.fully_filled is True


def test_vwap_insufficient_depth_is_not_fully_filled():
    # Only 0.5 share available against a 1-share request.
    fill = vwap_to_fill(_levels(("0.46", "0.5")), Decimal("1"))
    assert fill.filled_qty == Decimal("0.5")
    assert fill.fully_filled is False
    assert fill.avg_price == Decimal("0.46")  # VWAP over what *could* be filled


def test_vwap_empty_book():
    fill = vwap_to_fill((), Decimal("1"))
    assert fill.filled_qty == Decimal("0")
    assert fill.fully_filled is False
    assert fill.avg_price == Decimal("0")


# --- evaluate_long_set: the spec's worked example ----------------------------


def test_long_set_worked_example():
    # YES_ask 0.46 + NO_ask 0.49 = 0.95  ->  gross 5c; after 2c costs, net 3c.
    yes = _book(asks=_levels(("0.46", "5")))
    no = _book(asks=_levels(("0.49", "5")))
    sig = evaluate_long_set(yes, no, ArbParams(), market_id="m1", condition_id="c1", at=AT)

    assert sig is not None
    assert sig.kind == "long_set"
    assert sig.yes_price == Decimal("0.46")
    assert sig.no_price == Decimal("0.49")
    assert sig.set_size == Decimal("1")
    assert sig.gross_edge == Decimal("0.05")
    assert sig.estimated_costs == Decimal("0.02")  # gas 0.01 + slippage 0.01
    assert sig.net_edge == Decimal("0.03")
    assert sig.hypothetical_pnl == Decimal("0.03")  # net * set_size (1)
    assert sig.market_id == "m1"
    assert sig.condition_id == "c1"
    assert sig.time == AT
    # Money types must be Decimal, never float.
    assert isinstance(sig.gross_edge, Decimal)
    assert isinstance(sig.net_edge, Decimal)
    assert isinstance(sig.hypothetical_pnl, Decimal)


def test_long_set_vwap_over_depth_feeds_the_edge():
    # YES VWAP over two levels: 0.4 @ 0.46 + 0.6 @ 0.47 = 0.466; NO ask 0.49.
    #   combined 0.956 -> gross 0.044 -> net 0.024 after 2c costs.
    yes = _book(asks=_levels(("0.46", "0.4"), ("0.47", "1.0")))
    no = _book(asks=_levels(("0.49", "5")))
    sig = evaluate_long_set(yes, no, ArbParams(), market_id="m1", condition_id="c1", at=AT)

    assert sig is not None
    assert sig.yes_price == Decimal("0.466")
    assert sig.no_price == Decimal("0.49")
    assert sig.gross_edge == Decimal("0.044")
    assert sig.net_edge == Decimal("0.024")


def test_long_set_no_arb_when_sum_at_or_above_one():
    yes = _book(asks=_levels(("0.51", "5")))
    no = _book(asks=_levels(("0.50", "5")))  # sum 1.01 -> no edge
    assert evaluate_long_set(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None


def test_long_set_below_cost_threshold_is_rejected():
    # gross 1c (0.99) < 2c costs -> net negative -> not flagged.
    yes = _book(asks=_levels(("0.49", "5")))
    no = _book(asks=_levels(("0.50", "5")))
    assert evaluate_long_set(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None


def test_long_set_exactly_at_threshold_is_rejected():
    # gross 2c == 2c costs -> net 0 -> strict ">" gate rejects.
    yes = _book(asks=_levels(("0.49", "5")))
    no = _book(asks=_levels(("0.49", "5")))
    assert evaluate_long_set(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None


def test_long_set_insufficient_depth_is_rejected():
    # Price looks great but only 0.5 share of YES is available for a 1-share set.
    yes = _book(asks=_levels(("0.40", "0.5")))
    no = _book(asks=_levels(("0.49", "5")))
    assert evaluate_long_set(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None


def test_long_set_min_net_edge_gate():
    # Same as the worked example (net 3c) but require >4c net -> rejected.
    yes = _book(asks=_levels(("0.46", "5")))
    no = _book(asks=_levels(("0.49", "5")))
    params = ArbParams(min_net_edge=Decimal("0.04"))
    assert evaluate_long_set(yes, no, params, market_id="m", condition_id="c", at=AT) is None


# --- evaluate_short_set -------------------------------------------------------


def test_short_set_worked_example():
    # YES_bid 0.55 + NO_bid 0.52 = 1.07  ->  gross 7c; after 2c costs, net 5c.
    yes = _book(bids=_levels(("0.55", "5")))
    no = _book(bids=_levels(("0.52", "5")))
    sig = evaluate_short_set(yes, no, ArbParams(), market_id="m2", condition_id="c2", at=AT)

    assert sig is not None
    assert sig.kind == "short_set"
    assert sig.yes_price == Decimal("0.55")
    assert sig.no_price == Decimal("0.52")
    assert sig.gross_edge == Decimal("0.07")
    assert sig.estimated_costs == Decimal("0.02")
    assert sig.net_edge == Decimal("0.05")
    assert sig.hypothetical_pnl == Decimal("0.05")


def test_short_set_no_arb_when_sum_at_or_below_one():
    yes = _book(bids=_levels(("0.49", "5")))
    no = _book(bids=_levels(("0.48", "5")))  # sum 0.97 -> no edge
    assert evaluate_short_set(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None


# --- evaluate_market dispatch -------------------------------------------------


def test_evaluate_market_returns_long_when_asks_cheap():
    yes = _book(asks=_levels(("0.46", "5")), bids=_levels(("0.40", "5")))
    no = _book(asks=_levels(("0.49", "5")), bids=_levels(("0.40", "5")))
    sig = evaluate_market(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT)
    assert sig is not None and sig.kind == "long_set"


def test_evaluate_market_returns_short_when_bids_rich():
    yes = _book(asks=_levels(("0.60", "5")), bids=_levels(("0.55", "5")))
    no = _book(asks=_levels(("0.60", "5")), bids=_levels(("0.52", "5")))
    sig = evaluate_market(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT)
    assert sig is not None and sig.kind == "short_set"


def test_evaluate_market_none_when_no_edge():
    yes = _book(asks=_levels(("0.51", "5")), bids=_levels(("0.49", "5")))
    no = _book(asks=_levels(("0.51", "5")), bids=_levels(("0.49", "5")))
    assert evaluate_market(yes, no, ArbParams(), market_id="m", condition_id="c", at=AT) is None
