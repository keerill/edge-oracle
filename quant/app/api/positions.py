"""POST /positions and GET /positions — the operator's portfolio of placed bets.

``POST`` records a bet the operator placed (manually on Polymarket), deriving ``shares`` from
``stake / entry_price``. ``GET`` returns every position with live valuation — open positions
marked at the side token's current midpoint — plus running totals (exposure, unrealized,
realized). Settlement (closing resolved positions) is done by the resolution-watcher.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.ingestion import store
from app.math.profit import mark_to_market
from app.models.position import (
    OpenPositionRequest,
    Position,
    PositionsResponse,
    PositionWithPnl,
)

router = APIRouter(prefix="/positions", tags=["positions"])

ZERO = Decimal(0)


@router.post("", response_model=Position, status_code=201)
async def create_position(
    body: OpenPositionRequest,
    session: AsyncSession = Depends(get_session),
) -> Position:
    """Record a placed bet. ``shares = stake / entry_price`` is derived server-side."""
    position = Position(
        id=str(uuid.uuid4()),
        created_at=datetime.now(tz=UTC),
        market_id=body.market_id,
        condition_id=body.condition_id,
        strategy=body.strategy,
        side=body.side,
        entry_price=body.entry_price,
        stake_usd=body.stake_usd,
        shares=body.stake_usd / body.entry_price,
        status="open",
        signal_id=body.signal_id,
    )
    await store.insert_position(session, position)
    await session.commit()
    return position


def _side_token_id(market, side: str) -> str | None:
    if side == "yes":
        return market.yes_token_id
    if side == "no":
        return market.no_token_id
    return None  # arb "set" positions have no single side token to mark


@router.get("", response_model=PositionsResponse)
async def list_positions(
    session: AsyncSession = Depends(get_session),
) -> PositionsResponse:
    """All positions with live valuation and running totals."""
    all_positions = await store.load_positions(session)
    markets = {m.market_id: m for m in await store.load_tracked_markets(session)}
    token_ids = [
        tid for m in markets.values() for tid in (m.yes_token_id, m.no_token_id)
    ]
    quotes = await store.load_latest_quotes(session, token_ids=token_ids or None)

    enriched: list[PositionWithPnl] = []
    total_exposure = ZERO
    total_unrealized = ZERO
    total_realized = ZERO
    for p in all_positions:
        if p.status == "closed":
            total_realized += p.pnl or ZERO
            enriched.append(PositionWithPnl(position=p))
            continue
        total_exposure += p.stake_usd
        market = markets.get(p.market_id)
        token_id = _side_token_id(market, p.side) if market else None
        quote = quotes.get(token_id) if token_id else None
        current_mid = quote.midpoint if quote else None
        unrealized = (
            mark_to_market(p.shares, current_mid, p.stake_usd)
            if current_mid is not None
            else None
        )
        if unrealized is not None:
            total_unrealized += unrealized
        enriched.append(
            PositionWithPnl(position=p, current_mid=current_mid, unrealized_pnl=unrealized)
        )

    return PositionsResponse(
        positions=enriched,
        total_exposure=total_exposure,
        total_unrealized_pnl=total_unrealized,
        total_realized_pnl=total_realized,
    )
