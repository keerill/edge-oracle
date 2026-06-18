"""Pure arb fill-check tests — the gating-math core. Sync, offline, deterministic.

Order books are hand-computed inline so every number checks by hand. All edges are exact
``Decimal``. Covers the survives/ok path plus each failure label (depth gone, edge collapsed,
flipped side, missing leg) and the exact latency computation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.math.arb import ArbParams
from app.models.book import BookLevel, OrderBook
from app.paper.fill_check import ArbFillCheck, check_arb_fill

ADVISED_AT = datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)
CHECKED_AT = ADVISED_AT + timedelta(seconds=12)  # 12s latency by default
PARAMS = ArbParams()  # default costs = gas 0.01 + slippage 0.01 = 0.02


def _levels(*pairs: tuple[str, str]) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in pairs)


def _book(*, bids=(), asks=(), token_id="t") -> OrderBook:
    return OrderBook(token_id=token_id, bids=bids, asks=asks)


def _check(advised_kind="long_set", *, yes_book, no_book, checked_at=CHECKED_AT) -> ArbFillCheck:
    return check_arb_fill(
        advised_kind=advised_kind,
        yes_book=yes_book,
        no_book=no_book,
        params=PARAMS,
        advised_at=ADVISED_AT,
        checked_at=checked_at,
    )


def test_long_arb_survives_is_ok() -> None:
    # asks 0.46 + 0.49 = 0.95 -> gross 0.05, net 0.05 - 0.02 = 0.03
    res = _check(yes_book=_book(asks=_levels(("0.46", "5"))), no_book=_book(asks=_levels(("0.49", "5"))))
    assert res.ok is True
    assert res.rechecked_net_edge == Decimal("0.03")
    assert isinstance(res.rechecked_net_edge, Decimal)
    assert res.reason == "ok"
    assert res.latency_s == Decimal("12")


def test_depth_gone_is_rejected() -> None:
    # only 0.5 share on the YES ask vs a 1-set request -> can't fully fill
    res = _check(
        yes_book=_book(asks=_levels(("0.46", "0.5"))),
        no_book=_book(asks=_levels(("0.49", "5"))),
    )
    assert res.ok is False
    assert res.rechecked_net_edge is None
    assert res.reason == "depth_gone"


def test_edge_collapsed_is_rejected() -> None:
    # asks 0.50 + 0.51 = 1.01 -> gross -0.01, fully fillable but no edge; no bids -> not flipped
    res = _check(
        yes_book=_book(asks=_levels(("0.50", "5"))),
        no_book=_book(asks=_levels(("0.51", "5"))),
    )
    assert res.ok is False
    assert res.reason == "edge_collapsed"


def test_flipped_side_is_rejected() -> None:
    # advised LONG, but now only bids exist summing > $1 -> the SHORT arb fires instead
    # bids 0.55 + 0.52 = 1.07 -> short gross 0.07, net 0.05; no asks -> long can't fill
    res = _check(
        advised_kind="long_set",
        yes_book=_book(bids=_levels(("0.55", "5"))),
        no_book=_book(bids=_levels(("0.52", "5"))),
    )
    assert res.ok is False
    assert res.reason == "flipped_side"


def test_missing_leg_is_no_book() -> None:
    # YES ask side empty -> the set can't be priced; no bids -> not flipped
    res = _check(
        yes_book=_book(asks=()),
        no_book=_book(asks=_levels(("0.49", "5"))),
    )
    assert res.ok is False
    assert res.reason == "no_book"


def test_short_arb_survives_is_ok() -> None:
    # advised SHORT: bids 0.55 + 0.52 = 1.07 -> gross 0.07, net 0.05
    res = _check(
        advised_kind="short_set",
        yes_book=_book(bids=_levels(("0.55", "5"))),
        no_book=_book(bids=_levels(("0.52", "5"))),
    )
    assert res.ok is True
    assert res.rechecked_net_edge == Decimal("0.05")
    assert res.reason == "ok"


def test_latency_sub_second_is_exact_decimal() -> None:
    res = _check(
        yes_book=_book(asks=_levels(("0.46", "5"))),
        no_book=_book(asks=_levels(("0.49", "5"))),
        checked_at=ADVISED_AT + timedelta(seconds=90, milliseconds=500),
    )
    assert res.latency_s == Decimal("90.5")
