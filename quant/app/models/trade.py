"""Canonical trade print — one row per executed trade on a tracked market.

A "trade print" is reference data (what actually traded), the companion to the
top-of-book ``QuoteSnapshot``. All money fields are ``Decimal`` and map 1:1 to the
``trades`` hypertable's NUMERIC columns. ``price``/``size`` are coerced once, in the
pure transform, from the wire **string** literal (never via float) — even though the
Data API serializes them as JSON numbers, we keep the exact reported value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Trade(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime  # trade time (UTC), from the print's unix timestamp
    token_id: str  # the ERC-1155 position (outcome) token that traded
    market_id: str  # resolved Gamma market id (caller maps token -> market)
    price: Decimal  # executed price in [0, 1]
    size: Decimal  # number of shares
    taker_side: str | None  # "BUY" | "SELL" (taker direction), as reported
    trade_id: str  # the on-chain transaction hash of the fill
