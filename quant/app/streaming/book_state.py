"""In-memory order-book state, updated by WebSocket deltas. Pure — no I/O, no clock.

A ``LiveBook`` holds one token's two sides as ``price -> size`` maps and applies the CLOB
market-channel frames: a full ``book`` snapshot replaces both sides; a ``price_change`` delta
upserts levels (size ``0`` removes a level). ``snapshot()`` materializes the canonical frozen
``OrderBook`` the arb math consumes.

Money discipline: every price/size becomes ``Decimal`` straight from the wire string (mirroring
``ingestion.transform.orderbook_from_raw``) — **never** via float. ``BookStore`` fans frames out
to the right token and maps a token back to the market whose arb must be re-evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models.book import BookLevel, OrderBook
from app.models.market import Market
from app.polymarket.schemas import RawWsBook, RawWsChange, RawWsPriceChange

# CLOB market-channel side labels.
_BUY = "BUY"  # bid side
_SELL = "SELL"  # ask side


class LiveBook:
    """One token's live order book as mutable ``price -> size`` maps (Decimal-native)."""

    __slots__ = ("token_id", "bids", "asks")

    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}

    def apply_book(self, raw: RawWsBook) -> None:
        """Replace both sides from a full snapshot frame."""
        self.bids = {Decimal(lvl.price): Decimal(lvl.size) for lvl in raw.bids}
        self.asks = {Decimal(lvl.price): Decimal(lvl.size) for lvl in raw.asks}

    def apply_price_change(self, raw: RawWsPriceChange) -> None:
        """Upsert each changed level; a level whose new size is 0 is removed."""
        for change in raw.changes:
            self._apply_change(change)

    def _apply_change(self, change: RawWsChange) -> None:
        side = self.bids if change.side == _BUY else self.asks
        price = Decimal(change.price)
        size = Decimal(change.size)
        if size == 0:
            side.pop(price, None)
        else:
            side[price] = size

    def snapshot(self, *, timestamp=None) -> OrderBook:
        """Materialize the canonical frozen ``OrderBook`` (best bid/ask stay defensive)."""
        return OrderBook(
            token_id=self.token_id,
            timestamp=timestamp,
            bids=tuple(BookLevel(price=p, size=s) for p, s in self.bids.items()),
            asks=tuple(BookLevel(price=p, size=s) for p, s in self.asks.items()),
        )


@dataclass(frozen=True)
class _TokenRef:
    """Which market a token belongs to, and which leg it is."""

    market: Market
    is_yes: bool


@dataclass
class BookStore:
    """Live books for every tracked token + the token->market index.

    ``apply`` routes a parsed WS frame to the right token's ``LiveBook`` and returns the
    ``market_id`` whose arb should be re-evaluated (or ``None`` for a frame about a token we
    don't track). Build it from the tracked universe with ``from_markets``.
    """

    index: dict[str, _TokenRef] = field(default_factory=dict)
    books: dict[str, LiveBook] = field(default_factory=dict)

    @classmethod
    def from_markets(cls, markets: list[Market]) -> BookStore:
        index: dict[str, _TokenRef] = {}
        for m in markets:
            index[m.yes_token_id] = _TokenRef(market=m, is_yes=True)
            index[m.no_token_id] = _TokenRef(market=m, is_yes=False)
        return cls(index=index)

    @property
    def token_ids(self) -> list[str]:
        return list(self.index)

    def apply(self, msg: RawWsBook | RawWsPriceChange) -> str | None:
        """Apply a frame to its token's book; return the affected ``market_id`` (or ``None``)."""
        ref = self.index.get(msg.asset_id)
        if ref is None:
            return None
        book = self.books.get(msg.asset_id)
        if book is None:
            book = LiveBook(msg.asset_id)
            self.books[msg.asset_id] = book
        if isinstance(msg, RawWsBook):
            book.apply_book(msg)
        else:
            book.apply_price_change(msg)
        return ref.market.market_id

    def market_books(self, market_id: str) -> tuple[Market, OrderBook, OrderBook] | None:
        """Both legs' snapshots for a market, or ``None`` if either leg has no book yet."""
        market = self._market(market_id)
        if market is None:
            return None
        yes = self.books.get(market.yes_token_id)
        no = self.books.get(market.no_token_id)
        if yes is None or no is None:
            return None
        return market, yes.snapshot(), no.snapshot()

    def _market(self, market_id: str) -> Market | None:
        for ref in self.index.values():
            if ref.market.market_id == market_id:
                return ref.market
        return None
