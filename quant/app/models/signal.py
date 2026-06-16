"""Canonical arbitrage signal — one detected complete-set opportunity.

Mirrors ``QuoteSnapshot``: frozen, ``Decimal``-native money that maps 1:1 to the
``signals`` table's NUMERIC columns (no float ever reaches the DB). Prices and edges
are per unit set (1 YES + 1 NO); ``hypothetical_pnl = net_edge * set_size``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

ArbKind = Literal["long_set", "short_set"]


class ArbSignal(BaseModel):
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
