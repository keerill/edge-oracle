"""Canonical order-book domain model (Decimal-native).

``best_bid``/``best_ask`` are computed defensively (max bid price / min ask price)
rather than trusting upstream ordering — cheap insurance against a CLOB ordering
change. Either side may be empty, in which case the corresponding best is ``None``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: Decimal
    size: Decimal


class OrderBook(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_id: str
    timestamp: datetime | None = None
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    @property
    def best_bid(self) -> BookLevel | None:
        return max(self.bids, key=lambda lvl: lvl.price) if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return min(self.asks, key=lambda lvl: lvl.price) if self.asks else None
