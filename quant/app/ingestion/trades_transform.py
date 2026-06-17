"""Pure raw->canonical transform for trade prints. NO I/O, NO clock, NO network.

The companion to ``transform.quote_from_book``: the single place trade-print wire strings are
coerced to ``Decimal`` (from the string, never a float). ``market_id`` is resolved by the caller
(token -> tracked-market mapping) since the Data API reports only the condition id + token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.trade import Trade
from app.polymarket.schemas import RawTrade


def trade_from_raw(raw: RawTrade, *, market_id: str) -> Trade:
    """Convert a validated raw trade print into the canonical Decimal-native ``Trade``."""
    return Trade(
        time=datetime.fromtimestamp(raw.timestamp, tz=UTC),
        token_id=raw.asset,
        market_id=market_id,
        price=Decimal(raw.price),  # raw.price is the exact wire literal as a str
        size=Decimal(raw.size),
        taker_side=raw.side,
        trade_id=raw.transactionHash,
    )
