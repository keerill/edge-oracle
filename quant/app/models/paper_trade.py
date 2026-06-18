"""Paper-trading journal model — the bet the advisor WOULD have placed, logged
automatically at advice time so it can later be scored against the real outcome.

This is the no-money validation counterpart to ``Position`` (which records bets the
operator actually placed manually). A ``PaperTrade`` is written by the auto-capture step
the moment a gated signal is surfaced, then settled by the resolution watcher when the
market resolves. All money is ``Decimal``-native (frozen model).

``p``/``p_lo`` are the claimed probability and its CI lower bound — populated only for
directional bets (``side`` in {"yes","no"}); set-arb is outcome-independent so they stay
``None``. ``outcome``/``realized_pnl``/``resolved_at`` stay ``None`` until settlement.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

PaperSide = Literal["yes", "no", "set"]
# open: awaiting resolution · closed: settled against a real outcome · expired: the
# market resolved but the advised fill could not be scored (e.g. a set-arb whose
# dislocation we never re-confirmed). Kept distinct so expired rows don't inflate P&L.
PaperStatus = Literal["open", "closed", "expired"]


class PaperTrade(BaseModel):
    """A logged advisory recommendation. ``advised_price`` is the all-in price we'd have
    paid per share (incl. half-spread), ``stake_usd`` what we'd have staked, ``shares``
    the derived quantity (``stake / advised_price``). ``edge`` is the expected per-position
    edge at advice time (net set-arb edge, or ``p_lo − advised_price`` for directional)."""

    model_config = ConfigDict(frozen=True)

    id: str
    advised_at: datetime
    strategy: str
    market_id: str
    condition_id: str
    side: PaperSide
    advised_price: Decimal
    stake_usd: Decimal
    shares: Decimal
    edge: Decimal
    p: Decimal | None = None
    p_lo: Decimal | None = None
    status: PaperStatus = "open"
    outcome: int | None = None
    realized_pnl: Decimal | None = None
    resolved_at: datetime | None = None
    signal_id: str | None = None
