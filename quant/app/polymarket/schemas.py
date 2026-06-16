"""Raw boundary schemas — the untrusted-input gate.

These models mirror Polymarket's *wire* shapes exactly, warts and all (stringified
JSON arrays, decimal values as strings). Their only job is structural validation:
"did these bytes parse into the shape we expect?". They do NOT coerce money to
Decimal and contain no business logic — that happens once, downstream, in the pure
``ingestion.transform`` layer, so there is a single audited money-coercion site.

Every model uses ``extra="ignore"``: Polymarket adds response fields frequently and
we must not break when it does (and must not trust them either).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# --- Gamma (market discovery) -------------------------------------------------


class RawGammaEventRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    title: str | None = None


class RawGammaMarket(BaseModel):
    # coerce_numbers_to_str: Gamma sometimes sends ``id`` as a JSON number; keep it
    # a string. Huge token/condition ids always arrive as strings already.
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    id: str
    question: str
    slug: str
    conditionId: str
    # ``outcomes`` is usually a real array but is sometimes a stringified JSON
    # array; ``clobTokenIds``/``outcomePrices`` are reliably stringified. Accept
    # both forms here and normalize in the transform.
    outcomes: list[str] | str = []
    outcomePrices: str | list[str] | None = None
    clobTokenIds: str | list[str] | None = None
    enableOrderBook: bool = False
    # Conservative defaults: a market missing these fields is treated as inactive /
    # closed / non-orderbook so it gets filtered out rather than silently tracked.
    active: bool = False
    closed: bool = True
    category: str | None = None
    liquidity: str | float | None = None
    liquidityNum: float | None = None
    volume24hr: float | None = None
    events: list[RawGammaEventRef] | None = None


# --- CLOB (order book + pricing) ----------------------------------------------


class RawBookLevel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    price: str  # decimal string — kept as str at the boundary
    size: str


class RawOrderBook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market: str | None = None  # condition id
    asset_id: str | None = None  # the token id this book is for
    timestamp: str | int | None = None
    hash: str | None = None
    bids: list[RawBookLevel] = []  # sent DESC by price
    asks: list[RawBookLevel] = []  # sent ASC by price


class RawPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    price: str


class RawMidpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    midpoint: str


class RawSpread(BaseModel):
    model_config = ConfigDict(extra="ignore")

    spread: str
    bid: str | None = None
    ask: str | None = None


class RawHistoryPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: int  # unix seconds
    p: float  # price as a float on the wire (only legitimate float; never stored)


class RawPricesHistory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    history: list[RawHistoryPoint] = []
