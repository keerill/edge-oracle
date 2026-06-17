"""Read-side view of the advisor's ``AdvisedSignal`` — the executor's OWN copy of the contract.

The executor consumes advisor opportunities as *data* off the Redis ``edge:signals`` channel
(``AdvisedSignal.model_dump_json()``), exactly as ``web/`` does via Zod. It deliberately does
NOT import ``quant`` (CLAUDE.md: layers talk over the wire, not a shared codebase). This is the
boundary model that validates that JSON; field names/types mirror ``quant/app/models/advisor.py``.
Money arrives as JSON strings and Pydantic coerces to ``Decimal`` (no float in the money path).
A golden-JSON contract test pins that this view parses a real advisor payload.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Strategy = Literal["extreme_correction", "favourite_longshot", "set_arb"]


class GateBreakdownView(BaseModel):
    model_config = ConfigDict(frozen=True)

    m: Decimal
    half_spread: Decimal
    slippage: Decimal
    gas: Decimal
    margin: Decimal
    p_lo: Decimal
    threshold: Decimal


class AdvisedSignalView(BaseModel):
    """Mirrors ``AdvisedSignal``; ``extra="ignore"`` tolerates additive contract drift."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    time: datetime
    market_id: str
    condition_id: str
    market_question: str | None = None
    strategy: Strategy
    kind: str

    market_price: Decimal
    p: Decimal | None = None
    edge: Decimal
    net_edge: Decimal

    recommended_size_usd: Decimal = Field(ge=0)
    recommended_size_pct: Decimal = Field(ge=0)
    confidence: Decimal = Field(ge=0, le=1)

    gate_passed: bool
    gate: GateBreakdownView | None = None
