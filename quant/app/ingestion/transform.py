"""Pure raw->canonical transforms. NO I/O, NO clock, NO network.

This is the single place where:
  * stringified JSON arrays (`clobTokenIds`, `outcomes`) are parsed, and
  * wire strings are coerced to ``Decimal`` (constructed from the string, never
    from a float — so no float ever enters the money path).

Keeping it pure makes the universe-selection and price-derivation logic fully
unit-testable without a database or the network. The capture time is injected.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from app.models.book import BookLevel, OrderBook
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.polymarket.schemas import RawGammaMarket, RawOrderBook


def parse_stringified_str_array(raw: str | list[str] | None) -> list[str]:
    """Normalize Gamma's ``"[\"a\", \"b\"]"`` / ``["a", "b"]`` / ``None`` into a list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        parsed = json.loads(s)
        if not isinstance(parsed, list):
            raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
        return [str(x) for x in parsed]
    raise TypeError(f"cannot parse a string array from {type(raw).__name__}")


def _to_decimal(value: str | float | None) -> Decimal | None:
    """Decimal from a wire value via ``str`` (never ``Decimal(float)``)."""
    if value is None:
        return None
    return Decimal(str(value))


def is_binary(outcomes: Sequence[str]) -> bool:
    """True iff outcomes are exactly Yes/No (case-insensitive, order-sensitive)."""
    return [o.strip().casefold() for o in outcomes] == ["yes", "no"]


def market_from_raw(raw: RawGammaMarket) -> Market:
    """Convert a validated raw Gamma market into the canonical Market.

    Raises ``ValueError`` if ``clobTokenIds`` does not contain exactly two ids
    (a structural requirement for a binary YES/NO market we can snapshot).
    """
    tokens = parse_stringified_str_array(raw.clobTokenIds)
    if len(tokens) != 2:
        raise ValueError(
            f"market {raw.id!r} has {len(tokens)} clobTokenIds, expected 2"
        )
    outcomes = tuple(parse_stringified_str_array(raw.outcomes))
    liq_raw = raw.liquidity if raw.liquidity is not None else raw.liquidityNum
    return Market(
        market_id=raw.id,
        condition_id=raw.conditionId,
        question=raw.question,
        slug=raw.slug,
        category=raw.category,
        event_id=raw.events[0].id if raw.events else None,
        outcomes=outcomes,
        yes_token_id=tokens[0],
        no_token_id=tokens[1],
        enable_order_book=raw.enableOrderBook,
        active=raw.active,
        closed=raw.closed,
        liquidity=_to_decimal(liq_raw),
    )


def _parse_book_timestamp(ts: str | int | None) -> datetime | None:
    """Best-effort parse of CLOB's book timestamp (unix ms, sometimes seconds)."""
    if ts is None:
        return None
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return None
    # Unix seconds now ~1.7e9 (10 digits); unix ms ~1.7e12 (13 digits).
    if n > 10_000_000_000:
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(n, tz=timezone.utc)


def orderbook_from_raw(raw: RawOrderBook, token_id: str) -> OrderBook:
    """Convert a validated raw book into a canonical Decimal-native OrderBook."""
    bids = tuple(
        BookLevel(price=Decimal(lvl.price), size=Decimal(lvl.size)) for lvl in raw.bids
    )
    asks = tuple(
        BookLevel(price=Decimal(lvl.price), size=Decimal(lvl.size)) for lvl in raw.asks
    )
    return OrderBook(
        token_id=token_id,
        timestamp=_parse_book_timestamp(raw.timestamp),
        bids=bids,
        asks=asks,
    )


def quote_from_book(book: OrderBook, *, market_id: str, at: datetime) -> QuoteSnapshot:
    """Derive a top-of-book snapshot. ``midpoint``/``spread`` are ``None`` when a
    side is missing — we record the tick regardless of one-sided/empty books."""
    bb = book.best_bid
    ba = book.best_ask
    best_bid = bb.price if bb is not None else None
    best_ask = ba.price if ba is not None else None
    if best_bid is not None and best_ask is not None:
        midpoint: Decimal | None = (best_bid + best_ask) / Decimal(2)
        spread: Decimal | None = best_ask - best_bid
    else:
        midpoint = None
        spread = None
    return QuoteSnapshot(
        time=at,
        token_id=book.token_id,
        market_id=market_id,
        best_bid=best_bid,
        best_bid_size=bb.size if bb is not None else None,
        best_ask=best_ask,
        best_ask_size=ba.size if ba is not None else None,
        midpoint=midpoint,
        spread=spread,
    )


def _liquidity_key(m: Market) -> Decimal:
    return m.liquidity if m.liquidity is not None else Decimal(0)


def _snapshotable(m: Market) -> bool:
    """We can only snapshot a market that is active, open, and order-book enabled."""
    return m.active and not m.closed and m.enable_order_book


def rank_and_select(
    markets: Sequence[Market], *, top_n: int, allowlist: Sequence[str] = ()
) -> list[Market]:
    """Select the tracked universe.

    With an allowlist: restrict to those condition ids (the override path also
    relaxes the YES/NO requirement, since the user chose them explicitly) and
    ignore ``top_n``. Otherwise: keep active+open+orderbook **binary** markets,
    ranked by liquidity desc, capped at ``top_n``.
    """
    if allowlist:
        allow = set(allowlist)
        selected = [m for m in markets if m.condition_id in allow and _snapshotable(m)]
        return sorted(selected, key=_liquidity_key, reverse=True)

    tradeable = [m for m in markets if _snapshotable(m) and is_binary(m.outcomes)]
    tradeable.sort(key=_liquidity_key, reverse=True)
    return tradeable[:top_n]
