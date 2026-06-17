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


class RawGammaTag(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    id: str | None = None
    label: str | None = None
    slug: str | None = None


class RawGammaEventRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    title: str | None = None
    # Tags are NOT returned by Gamma /markets (only by /events); populated by a
    # secondary fetch during discovery so the category can be derived. Empty when absent.
    tags: list[RawGammaTag] = []


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


# --- CLOB market WebSocket (live order-book deltas) ---------------------------
# The `market` channel emits full `book` snapshots and `price_change` deltas (plus
# `tick_size_change` / `last_trade_price`, which we ignore). Same untrusted-boundary
# discipline: prices/sizes stay `str`, money is coerced to Decimal only downstream.


class RawWsBook(BaseModel):
    """A full order-book snapshot frame (``event_type="book"``)."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    asset_id: str  # the token id this book is for
    market: str | None = None  # condition id
    timestamp: str | int | None = None
    bids: list[RawBookLevel] = []
    asks: list[RawBookLevel] = []


class RawWsChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    price: str
    size: str  # "0" => the level was removed
    side: str  # "BUY" (bid) | "SELL" (ask)


class RawWsPriceChange(BaseModel):
    """A book delta frame (``event_type="price_change"``): one or more level changes."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    asset_id: str
    market: str | None = None
    timestamp: str | int | None = None
    changes: list[RawWsChange] = []


def parse_ws_message(payload: dict) -> RawWsBook | RawWsPriceChange | None:
    """Dispatch a raw CLOB market-channel frame on ``event_type``.

    Returns the validated ``book``/``price_change`` model, or ``None`` for
    ``tick_size_change`` / ``last_trade_price`` / anything unrecognized (ignored —
    they don't move the book levels we price arb off of). Treats the payload as
    untrusted: a missing ``event_type`` or malformed shape yields ``None``.
    """
    event_type = payload.get("event_type")
    if event_type == "book":
        return RawWsBook.model_validate(payload)
    if event_type == "price_change":
        return RawWsPriceChange.model_validate(payload)
    return None


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


class RawTrade(BaseModel):
    # Data API ``/trades`` print. ``price``/``size`` arrive as JSON **numbers**; we keep
    # them as ``str`` here (coerce_numbers_to_str) so the single Decimal coercion happens
    # downstream in ``transform`` — and we parse the response with ``parse_float=str`` so the
    # exact wire literal is preserved (no float ever touches the money path).
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    asset: str  # the token id that traded
    conditionId: str
    side: str | None = None  # "BUY" | "SELL"
    size: str
    price: str
    timestamp: int  # unix seconds
    transactionHash: str
    outcome: str | None = None
    outcomeIndex: int | None = None


class RawHistoryPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: int  # unix seconds
    p: float  # price as a float on the wire (only legitimate float; never stored)


class RawPricesHistory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    history: list[RawHistoryPoint] = []
