"""Advisor view models — a detected signal enriched with live sizing for the dashboard.

The persisted ``signals`` row is a raw detection (price/edge_score/fair_value/net_edge);
``AdvisedSignal`` is the *advisor* view the REST layer serves: the same opportunity joined
with the current quote and run through the existing money math (``app.math.bet_sizing``) to
produce a recommended fractional-Kelly stake, the cost gate, and a confidence score.

Frozen and ``Decimal``-native like every money model — Pydantic v2 serializes ``Decimal`` to
JSON as a **string**, which is deliberate (no float ever touches the money path). The web Zod
boundary parses those strings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Strategy = Literal["extreme_correction", "favourite_longshot", "set_arb"]


class GateBreakdown(BaseModel):
    """The cost-gate components for a directional bet, so the UI can show *why* it passes or
    fails: the bet clears only when ``p_lo > threshold`` (strict). ``threshold`` is the all-in
    break-even ``m + half_spread + slippage + gas``; ``p_lo = p_side - margin`` (CI lower bound).
    """

    model_config = ConfigDict(frozen=True)

    m: Decimal  # side-token price you pay into (the side's midpoint)
    half_spread: Decimal  # spread / 2 of the side token
    slippage: Decimal
    gas: Decimal
    margin: Decimal  # model-error margin; p_lo = p_side - margin
    p_lo: Decimal  # CI lower bound the gate tests
    threshold: Decimal  # m + half_spread + slippage + gas (what p_lo must exceed)


class AdvisedSignal(BaseModel):
    """A detected signal enriched with sizing — one row of the Signals table / detail view.

    ``p`` is your probability for the side you'd buy (directional only; ``None`` for risk-free
    arb and for the probability-free favourite-longshot heuristic). ``net_edge`` is the sort
    key: the conservative net-of-cost edge the gate actually tests for directional, the locked
    ``net_edge`` for arb, the ``edge_score`` for longshot. ``confidence`` is a per-strategy
    heuristic in ``[0, 1]`` (see ``app.advisor.view``)."""

    model_config = ConfigDict(frozen=True)

    id: str  # synthesized stable id: f"{strategy}:{market_id}:{epoch_ms}"
    time: datetime
    market_id: str
    condition_id: str
    market_question: str | None = None  # human-readable market title (None if market untracked)
    strategy: Strategy
    kind: str  # per-strategy subtype/side ("correction" / "buy_yes" / "long_set" / ...)

    market_price: Decimal  # the "m" shown in the table (side price / set cost / YES price)
    p: Decimal | None  # your probability for the side you'd buy (directional only)
    edge: Decimal  # gross edge (over the ask for directional; gross_edge / edge_score otherwise)
    net_edge: Decimal  # net-of-cost edge (the sort key)

    recommended_size_usd: Decimal = Field(ge=0)  # fractional-Kelly stake (0 when gated/unsizable)
    recommended_size_pct: Decimal = Field(ge=0)  # recommended_size_usd / bankroll
    confidence: Decimal = Field(ge=0, le=1)  # per-strategy heuristic in [0, 1]

    gate_passed: bool
    gate: GateBreakdown | None  # populated for directional; None for arb / longshot
