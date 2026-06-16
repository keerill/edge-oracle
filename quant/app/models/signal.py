"""Canonical signal models — one detected opportunity per row, across strategies.

All are frozen, ``Decimal``-native, and map to a subset of the ``signals`` table's
NUMERIC columns (no float ever reaches the DB). Each strategy fills only its own columns;
``store.insert_signals`` persists a homogeneous batch via ``model_dump()``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

ArbKind = Literal["long_set", "short_set"]
LongshotSide = Literal["buy_yes", "buy_no"]


class ArbSignal(BaseModel):
    """Set-arb opportunity. Prices and edges are per unit set (1 YES + 1 NO);
    ``hypothetical_pnl = net_edge * set_size``."""

    model_config = ConfigDict(frozen=True)

    time: datetime  # capture time (UTC), injected by the scanner
    market_id: str
    condition_id: str
    kind: ArbKind  # "long_set" (buy set < $1) | "short_set" (mint + sell set > $1)
    yes_price: Decimal  # executed VWAP for the set on the YES leg (ask=long / bid=short)
    no_price: Decimal  # executed VWAP for the set on the NO leg
    set_size: Decimal  # number of complete sets the prices/edges are quoted for
    gross_edge: Decimal  # per set, before costs
    estimated_costs: Decimal  # per set (gas + slippage)
    net_edge: Decimal  # per set = gross_edge - estimated_costs
    hypothetical_pnl: Decimal  # net_edge * set_size


class FavouriteLongshotSignal(BaseModel):
    """Favourite-longshot bias signal — back the favourite (``buy_yes``) or fade the
    overpriced longshot (``buy_no``). ``edge_score`` is a normalized [0, 1] strength."""

    model_config = ConfigDict(frozen=True)

    time: datetime  # capture time (UTC), injected by the caller
    market_id: str
    condition_id: str
    strategy: Literal["favourite_longshot"] = "favourite_longshot"
    kind: LongshotSide  # "buy_yes" (back the favourite) | "buy_no" (fade the longshot)
    price: Decimal  # the market YES price m that triggered the signal
    edge_score: Decimal  # normalized bias strength in [0, 1] (higher = stronger)


class ExtremeCorrectionSignal(BaseModel):
    """Price-extreme correction — nudges an extreme implied probability toward 0.50 and
    exposes ``fair_value`` (the corrected estimate, a future fair-value input)."""

    model_config = ConfigDict(frozen=True)

    time: datetime
    market_id: str
    condition_id: str
    strategy: Literal["extreme_correction"] = "extreme_correction"
    kind: Literal["correction"] = "correction"
    price: Decimal  # raw implied probability m (the market price)
    fair_value: Decimal  # corrected probability, nudged toward 0.50


# Any persisted signal. ``store.insert_signals`` takes a homogeneous batch of one of
# these (a single call shares one shape, since the insert keys off the first row).
Signal = ArbSignal | FavouriteLongshotSignal | ExtremeCorrectionSignal
