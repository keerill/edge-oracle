"""GET /paper-performance — the no-money track record of the live advisor.

Scores the settled ``paper_trades`` (the bets the system would have placed, scored against
real outcomes) into the same metric shape as the backtest. Read the per-strategy breakdown,
not just the headline: set-arb paper P&L is fill-optimistic (``arb_fill_assumed``), while the
directional track is outcome-verified.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.config import effective_config
from app.api.deps import get_app_settings, get_session
from app.config import Settings
from app.ingestion import store
from app.math.paper_performance import summarize_paper_trades
from app.models.paper_performance import PaperPerformance

router = APIRouter(tags=["paper"])


@router.get("/paper-performance", response_model=PaperPerformance)
async def get_paper_performance(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> PaperPerformance:
    """The advisor's paper-trading scorecard against real outcomes (zero-bet report until the
    first paper trade settles). Initial bankroll is the operator's configured bankroll."""
    cfg = await effective_config(session, settings)
    trades = await store.load_paper_trades(session)  # all statuses
    closed = [t for t in trades if t.status == "closed"]
    n_open = sum(1 for t in trades if t.status == "open")
    arb = [t for t in trades if t.strategy == "set_arb"]
    return summarize_paper_trades(
        closed, initial_bankroll=cfg.bankroll, n_open=n_open, arb_trades=arb
    )
