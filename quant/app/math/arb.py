"""Pure YES/NO set-arbitrage math (complete-set rebalancing).

A Polymarket binary market has a YES and a NO token; 1 YES + 1 NO is a *complete set*
that redeems for exactly $1.00. Two risk-free edges open when the book dislocates:

  LONG  set: buy YES + buy NO for < $1.00, redeem the set for $1.
  SHORT set: mint a set for $1 (Split), sell YES + sell NO for > $1.00.

Everything here is pure: ``Decimal`` in, ``Decimal``/``ArbSignal`` out — no I/O, no
clock (the capture time is injected), no ``Settings``. Prices used are the *executed*
VWAP (the ask you pay / the bid you receive), never the midpoint, per the money-math rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.book import BookLevel, OrderBook
from app.models.signal import ArbKind, ArbSignal

ONE = Decimal(1)


class ArbParams(BaseModel):
    """Set-arb knobs. ``costs`` (gas + slippage) IS the threshold: an opportunity is
    flagged only when ``net_edge = gross - costs`` exceeds ``min_net_edge`` (an extra
    profit gate, off by default). All money is ``Decimal``."""

    model_config = ConfigDict(frozen=True)

    set_size: Decimal = Decimal(1)  # complete sets to price the edge for (1 YES + 1 NO)
    gas: Decimal = Decimal("0.01")  # per-set on-chain cost estimate (split/merge/redeem)
    slippage: Decimal = Decimal("0.01")  # per-set buffer beyond modeled book depth
    min_net_edge: Decimal = Decimal(0)  # extra profit gate; flag only when net > this

    @property
    def costs(self) -> Decimal:
        return self.gas + self.slippage


@dataclass(frozen=True)
class Fill:
    """Result of walking a book to fill a target quantity."""

    avg_price: Decimal  # size-weighted average price over what was filled (0 if nothing)
    filled_qty: Decimal  # quantity actually fillable (<= requested)
    fully_filled: bool  # filled_qty >= requested quantity


def vwap_to_fill(levels: Sequence[BookLevel], quantity: Decimal) -> Fill:
    """VWAP to fill ``quantity`` by walking ``levels`` **in the order given**.

    Callers pass levels in execution order: asks ascending to BUY, bids descending to
    SELL. Takes ``min(remaining, level.size)`` per level. ``fully_filled`` is False when
    the book lacks the depth to complete the fill.
    """
    remaining = quantity
    cost = Decimal(0)
    filled = Decimal(0)
    for lvl in levels:
        if remaining <= 0:
            break
        take = min(remaining, lvl.size)
        cost += take * lvl.price
        filled += take
        remaining -= take
    avg_price = cost / filled if filled > 0 else Decimal(0)
    return Fill(avg_price=avg_price, filled_qty=filled, fully_filled=filled >= quantity)


def _signal(
    *,
    kind: ArbKind,
    yes_price: Decimal,
    no_price: Decimal,
    gross: Decimal,
    params: ArbParams,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> ArbSignal | None:
    """Apply the cost gate and build the signal, or ``None`` if not profitable.

    Flag only when ``net = gross - costs`` strictly exceeds ``min_net_edge`` (default 0,
    i.e. costs are the whole threshold).
    """
    net = gross - params.costs
    if net <= params.min_net_edge:
        return None
    return ArbSignal(
        time=at,
        market_id=market_id,
        condition_id=condition_id,
        kind=kind,
        yes_price=yes_price,
        no_price=no_price,
        set_size=params.set_size,
        gross_edge=gross,
        estimated_costs=params.costs,
        net_edge=net,
        hypothetical_pnl=net * params.set_size,
    )


def evaluate_long_set(
    yes_book: OrderBook,
    no_book: OrderBook,
    params: ArbParams,
    *,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> ArbSignal | None:
    """LONG set: buy YES + buy NO for < $1.00. Walk the asks (cheapest first, sorted
    defensively) to fill ``set_size`` of each; flag when ``1 - (YES + NO)`` clears costs.
    Returns ``None`` if either leg can't be fully filled or the edge is too thin."""
    yes = vwap_to_fill(sorted(yes_book.asks, key=lambda lvl: lvl.price), params.set_size)
    no = vwap_to_fill(sorted(no_book.asks, key=lambda lvl: lvl.price), params.set_size)
    if not (yes.fully_filled and no.fully_filled):
        return None
    gross = ONE - (yes.avg_price + no.avg_price)
    return _signal(
        kind="long_set",
        yes_price=yes.avg_price,
        no_price=no.avg_price,
        gross=gross,
        params=params,
        market_id=market_id,
        condition_id=condition_id,
        at=at,
    )


def evaluate_short_set(
    yes_book: OrderBook,
    no_book: OrderBook,
    params: ArbParams,
    *,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> ArbSignal | None:
    """SHORT set: mint a set for $1 (Split) and sell YES + sell NO for > $1.00. Walk
    the bids (highest first, sorted defensively) to fill ``set_size`` of each; flag when
    ``(YES + NO) - 1`` clears costs. ``None`` if a leg can't fill or the edge is too thin."""
    yes = vwap_to_fill(
        sorted(yes_book.bids, key=lambda lvl: lvl.price, reverse=True), params.set_size
    )
    no = vwap_to_fill(
        sorted(no_book.bids, key=lambda lvl: lvl.price, reverse=True), params.set_size
    )
    if not (yes.fully_filled and no.fully_filled):
        return None
    gross = (yes.avg_price + no.avg_price) - ONE
    return _signal(
        kind="short_set",
        yes_price=yes.avg_price,
        no_price=no.avg_price,
        gross=gross,
        params=params,
        market_id=market_id,
        condition_id=condition_id,
        at=at,
    )


def evaluate_market(
    yes_book: OrderBook,
    no_book: OrderBook,
    params: ArbParams,
    *,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> ArbSignal | None:
    """The whole-market check. LONG and SHORT are mutually exclusive (since bid <= ask,
    Σbid <= Σask), so at most one fires; check LONG first, else SHORT."""
    long = evaluate_long_set(
        yes_book, no_book, params, market_id=market_id, condition_id=condition_id, at=at
    )
    if long is not None:
        return long
    return evaluate_short_set(
        yes_book, no_book, params, market_id=market_id, condition_id=condition_id, at=at
    )
