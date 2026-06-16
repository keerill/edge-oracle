"""LiveBook / BookStore tests — the in-memory order book updated by WS deltas.

Offline, deterministic. Asserts: a ``book`` snapshot replaces both sides; ``price_change``
deltas add / update / remove levels; prices and sizes are exact ``Decimal`` straight from the
wire string (never float); and BookStore routes a frame to the right token + returns the
affected market id.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.market import Market
from app.polymarket.schemas import RawWsBook, RawWsChange, RawWsPriceChange
from app.streaming.book_state import BookStore, LiveBook


def _book_frame(asset_id: str, *, bids=(), asks=()) -> RawWsBook:
    return RawWsBook(
        event_type="book",
        asset_id=asset_id,
        bids=[{"price": p, "size": s} for p, s in bids],
        asks=[{"price": p, "size": s} for p, s in asks],
    )


def _change_frame(asset_id: str, *changes: tuple[str, str, str]) -> RawWsPriceChange:
    return RawWsPriceChange(
        event_type="price_change",
        asset_id=asset_id,
        changes=[RawWsChange(price=p, size=s, side=side) for p, s, side in changes],
    )


def _market(market_id="m1", yes="yes-tok", no="no-tok") -> Market:
    return Market(
        market_id=market_id,
        condition_id=f"c-{market_id}",
        question="Q?",
        slug="q",
        category=None,
        event_id=None,
        yes_token_id=yes,
        no_token_id=no,
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=None,
    )


# --- LiveBook -----------------------------------------------------------------


def test_book_snapshot_replaces_both_sides_with_exact_decimals():
    book = LiveBook("yes-tok")
    book.apply_book(_book_frame("yes-tok", bids=[("0.46", "100")], asks=[("0.48", "200")]))
    snap = book.snapshot()

    assert snap.best_bid is not None and snap.best_ask is not None
    assert snap.best_bid.price == Decimal("0.46")
    assert snap.best_bid.size == Decimal("100")
    assert snap.best_ask.price == Decimal("0.48")
    # Exact Decimal from the wire string — not the float repr.
    assert str(snap.best_bid.price) == "0.46"


def test_second_book_snapshot_fully_replaces_the_first():
    book = LiveBook("yes-tok")
    book.apply_book(_book_frame("yes-tok", bids=[("0.40", "1")], asks=[("0.60", "1")]))
    book.apply_book(_book_frame("yes-tok", bids=[("0.55", "9")], asks=[("0.57", "9")]))
    snap = book.snapshot()

    assert snap.best_bid is not None and snap.best_bid.price == Decimal("0.55")
    assert snap.best_ask is not None and snap.best_ask.price == Decimal("0.57")
    assert len(snap.bids) == 1 and len(snap.asks) == 1  # the 0.40/0.60 levels are gone


def test_price_change_adds_updates_and_removes_levels():
    book = LiveBook("yes-tok")
    book.apply_book(_book_frame("yes-tok", bids=[("0.46", "100")], asks=[("0.48", "200")]))

    # Add a new bid level, resize the existing ask, remove the top bid (size 0).
    book.apply_price_change(
        _change_frame(
            "yes-tok",
            ("0.45", "50", "BUY"),  # add
            ("0.48", "250", "SELL"),  # update
            ("0.46", "0", "BUY"),  # remove
        )
    )
    snap = book.snapshot()

    bids = {lvl.price: lvl.size for lvl in snap.bids}
    asks = {lvl.price: lvl.size for lvl in snap.asks}
    assert bids == {Decimal("0.45"): Decimal("50")}  # 0.46 removed, 0.45 added
    assert asks == {Decimal("0.48"): Decimal("250")}  # resized


def test_removing_a_missing_level_is_a_noop():
    book = LiveBook("yes-tok")
    book.apply_book(_book_frame("yes-tok", bids=[("0.46", "100")]))
    book.apply_price_change(_change_frame("yes-tok", ("0.99", "0", "BUY")))  # not present
    snap = book.snapshot()
    assert {lvl.price for lvl in snap.bids} == {Decimal("0.46")}


# --- BookStore ----------------------------------------------------------------


def test_store_routes_frame_to_token_and_returns_market_id():
    store = BookStore.from_markets([_market("m1", yes="yes-tok", no="no-tok")])
    assert set(store.token_ids) == {"yes-tok", "no-tok"}

    affected = store.apply(_book_frame("yes-tok", asks=[("0.46", "10")]))
    assert affected == "m1"


def test_store_ignores_unknown_token():
    store = BookStore.from_markets([_market("m1", yes="yes-tok", no="no-tok")])
    assert store.apply(_book_frame("other-tok", asks=[("0.5", "1")])) is None


def test_market_books_none_until_both_legs_seen():
    store = BookStore.from_markets([_market("m1", yes="yes-tok", no="no-tok")])
    store.apply(_book_frame("yes-tok", asks=[("0.46", "10")]))
    assert store.market_books("m1") is None  # NO leg not seen yet

    store.apply(_book_frame("no-tok", asks=[("0.49", "10")]))
    got = store.market_books("m1")
    assert got is not None
    market, yes_book, no_book = got
    assert market.market_id == "m1"
    assert yes_book.best_ask is not None and yes_book.best_ask.price == Decimal("0.46")
    assert no_book.best_ask is not None and no_book.best_ask.price == Decimal("0.49")
