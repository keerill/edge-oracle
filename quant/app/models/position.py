"""Portfolio position models — the bets the operator has actually placed (manually on
Polymarket), tracked for live P&L and exposure.

``Position`` is the stored row. ``OpenPositionRequest`` is the POST body (the server stamps
``id``/``created_at``/``shares``). ``PositionWithPnl`` enriches an open position with its live
mark-to-market value; ``PositionsResponse`` bundles the list with running totals. All money is
``Decimal``-native (frozen models).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PositionSide = Literal["yes", "no", "set"]
PositionStatus = Literal["open", "closed"]


class OpenPositionRequest(BaseModel):
    """Record a placed bet. ``entry_price`` is the all-in price you actually paid per share;
    ``stake_usd`` is what you put in. ``signal_id`` optionally links the advised signal it came
    from. ``shares`` is derived server-side (``stake / entry_price``)."""

    model_config = ConfigDict(frozen=True)

    market_id: str
    condition_id: str
    strategy: str
    side: PositionSide
    entry_price: Decimal = Field(gt=0, le=1)
    stake_usd: Decimal = Field(ge=0)
    signal_id: str | None = None


class Position(BaseModel):
    """A stored portfolio position. ``outcome``/``pnl``/``resolved_at`` are set only once the
    market resolves and the position is settled (``status="closed"``)."""

    model_config = ConfigDict(frozen=True)

    id: str
    created_at: datetime
    market_id: str
    condition_id: str
    strategy: str
    side: PositionSide
    entry_price: Decimal
    stake_usd: Decimal
    shares: Decimal
    status: PositionStatus
    outcome: int | None = None
    pnl: Decimal | None = None
    resolved_at: datetime | None = None
    signal_id: str | None = None


class PositionWithPnl(BaseModel):
    """A position plus its live valuation: ``unrealized_pnl`` (open, marked at the current
    midpoint) or the realized ``pnl`` (closed). ``current_mid`` is the side token's latest
    midpoint, ``None`` when no quote is available."""

    model_config = ConfigDict(frozen=True)

    position: Position
    current_mid: Decimal | None = None
    unrealized_pnl: Decimal | None = None


class PositionsResponse(BaseModel):
    """The portfolio view: every position with valuation, plus running totals.

    ``total_exposure`` is the staked capital still open; ``total_unrealized_pnl`` marks the open
    book at current midpoints; ``total_realized_pnl`` sums closed P&L."""

    model_config = ConfigDict(frozen=True)

    positions: list[PositionWithPnl]
    total_exposure: Decimal
    total_unrealized_pnl: Decimal
    total_realized_pnl: Decimal
