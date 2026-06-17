"""Stream-engine tests driven by a *mock WS feed* (an async iterator of raw frames).

Asserts the live arb path end-to-end without any socket or Redis: a cheap complete set
publishes one AdvisedSignal with the hand-checked net edge and the stable id; no edge ->
no publish; an unchanged book re-delta does not republish (dedup); and a malformed frame
is skipped without killing the loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.models.advisor import AdvisedSignal
from app.models.market import Market
from app.streaming.book_state import BookStore
from app.streaming.engine import arb_params, run_stream

AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)


async def _feed(frames: list[dict]) -> AsyncIterator[dict]:
    for f in frames:
        yield f


def _market(market_id="m1", yes="yes-tok", no="no-tok") -> Market:
    return Market(
        market_id=market_id,
        condition_id=f"c-{market_id}",
        question="Will it?",
        slug="will-it",
        category=None,
        event_id=None,
        yes_token_id=yes,
        no_token_id=no,
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=None,
    )


def _book(asset_id: str, *, bids=(), asks=()) -> dict:
    return {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def _change(asset_id: str, *changes: tuple[str, str, str]) -> dict:
    return {
        "event_type": "price_change",
        "asset_id": asset_id,
        "changes": [{"price": p, "size": s, "side": side} for p, s, side in changes],
    }


def _store() -> BookStore:
    return BookStore.from_markets([_market()])


# ArbParams from defaults: gas 0.01 + slippage 0.01 = 0.02 costs, min_net_edge 0.
PARAMS = arb_params(Settings(_env_file=None))


async def _run(frames: list[dict], dedup=None) -> list[AdvisedSignal]:
    published: list[AdvisedSignal] = []

    async def publish(advised: AdvisedSignal) -> None:
        published.append(advised)

    await run_stream(
        _feed(frames),
        _store(),
        PARAMS,
        publish=publish,
        bankroll=Decimal(1000),
        now=lambda: AT,
        dedup=dedup,
    )
    return published


# --- worked example -----------------------------------------------------------


async def test_cheap_set_publishes_one_signal_with_net_3c():
    # YES ask 0.46 + NO ask 0.49 = 0.95 -> gross 0.05, net 0.05 - 0.02 = 0.03.
    frames = [
        _book("yes-tok", asks=[("0.46", "100")]),
        _book("no-tok", asks=[("0.49", "100")]),  # both legs present -> evaluate fires here
    ]
    published = await _run(frames)

    assert len(published) == 1
    sig = published[0]
    assert sig.id == "set_arb:m1"
    assert sig.strategy == "set_arb"
    assert sig.kind == "long_set"
    assert sig.net_edge == Decimal("0.03")
    assert sig.edge == Decimal("0.05")  # gross
    assert sig.market_question == "Will it?"
    assert sig.confidence == Decimal(1)


async def test_no_edge_publishes_nothing():
    # 0.50 + 0.52 = 1.02 -> long gross negative; bids absent -> no short. No signal.
    frames = [
        _book("yes-tok", asks=[("0.50", "100")]),
        _book("no-tok", asks=[("0.52", "100")]),
    ]
    assert await _run(frames) == []


async def test_thin_book_below_set_size_publishes_nothing():
    # Cheap prices but only 0.5 of depth each — set_size 1 can't fully fill -> rejected.
    frames = [
        _book("yes-tok", asks=[("0.46", "0.5")]),
        _book("no-tok", asks=[("0.49", "0.5")]),
    ]
    assert await _run(frames) == []


async def test_unchanged_book_does_not_republish():
    # Same cheap set, then a delta that re-states the same NO ask size -> net edge unchanged.
    frames = [
        _book("yes-tok", asks=[("0.46", "100")]),
        _book("no-tok", asks=[("0.49", "100")]),  # publish #1
        _change("no-tok", ("0.49", "100", "SELL")),  # identical -> dedup, no publish
    ]
    published = await _run(frames)
    assert len(published) == 1


async def test_changed_edge_republishes():
    frames = [
        _book("yes-tok", asks=[("0.46", "100")]),
        _book("no-tok", asks=[("0.49", "100")]),  # net 0.03
        _change("no-tok", ("0.49", "0", "SELL")),  # remove the 0.49 ask...
        _change("no-tok", ("0.47", "100", "SELL")),  # ...new top 0.47 -> net 0.05
    ]
    published = await _run(frames)
    assert [s.net_edge for s in published] == [Decimal("0.03"), Decimal("0.05")]


async def test_malformed_frame_is_skipped_then_loop_continues():
    frames = [
        {"event_type": "book"},  # missing asset_id -> raises in apply -> skipped
        _book("yes-tok", asks=[("0.46", "100")]),
        _book("no-tok", asks=[("0.49", "100")]),  # still works after the bad frame
    ]
    published = await _run(frames)
    assert len(published) == 1
    assert published[0].net_edge == Decimal("0.03")


async def test_ignored_event_types_are_noops():
    frames = [
        {"event_type": "tick_size_change", "asset_id": "yes-tok"},
        {"event_type": "last_trade_price", "asset_id": "yes-tok"},
        _book("yes-tok", asks=[("0.46", "100")]),
        _book("no-tok", asks=[("0.49", "100")]),
    ]
    published = await _run(frames)
    assert len(published) == 1
