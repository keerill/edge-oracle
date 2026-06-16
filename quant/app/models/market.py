"""Canonical Market domain model (clean, typed, Decimal-native).

Distinct from ``polymarket.schemas.RawGammaMarket`` (the wire shape). Token and
condition ids are uint256/opaque and kept as ``str`` — never parsed as int.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Market(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str  # == Gamma `id`
    condition_id: str  # on-chain condition id (used by Data API)
    question: str
    slug: str
    category: str | None
    event_id: str | None
    outcomes: tuple[str, ...]  # e.g. ("Yes", "No"); used to filter the universe
    yes_token_id: str  # clobTokenIds[0] — uint256 string
    no_token_id: str  # clobTokenIds[1] — uint256 string
    enable_order_book: bool
    active: bool
    closed: bool
    liquidity: Decimal | None  # for ranking; Decimal, never float
