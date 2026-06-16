"""Canonical top-of-book quote snapshot — one row per token per tick.

``midpoint``/``spread`` are derived from the top of book and are ``None`` when a
side is missing (we never fabricate a price). All money fields are ``Decimal`` and
map 1:1 to the ``quotes`` hypertable's NUMERIC columns.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class QuoteSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime  # capture time (UTC), injected by the scanner
    token_id: str
    market_id: str
    best_bid: Decimal | None
    best_bid_size: Decimal | None
    best_ask: Decimal | None
    best_ask_size: Decimal | None
    midpoint: Decimal | None  # (best_bid + best_ask) / 2
    spread: Decimal | None  # best_ask - best_bid
