"""Pure favourite-longshot bias signal.

The favourite-longshot bias: bettors systematically overprice longshots and underprice
favourites. So when the YES price ``m`` sits in the *favourite* band we back YES (the
underpriced favourite); when ``m`` is a cheap *longshot* we buy NO (fade the overpriced
longshot). The dead gaps between the bands and the true extremes — where the bid/ask
spread eats any edge — emit nothing.

``edge_score`` is a normalized [0, 1] strength: it grows toward the more mispriced end of
each band (the heavier favourite / the more extreme longshot). It is a heuristic strength,
not a probability or expected value — fair-value modeling comes later.

Everything here is pure: ``Decimal`` in, ``FavouriteLongshotSignal``/``None`` out — no I/O,
no clock (the capture time is injected), no ``Settings``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.signal import FavouriteLongshotSignal

ZERO = Decimal(0)
ONE = Decimal(1)


class LongshotParams(BaseModel):
    """Favourite-longshot bands (closed intervals). Prices outside both bands — the dead
    gaps and the true extremes — emit no signal. All values are ``Decimal``."""

    model_config = ConfigDict(frozen=True)

    longshot_lo: Decimal = Decimal("0.05")  # buy-NO band low edge (truer extremes below it skip)
    longshot_hi: Decimal = Decimal("0.15")  # buy-NO band high edge
    favourite_lo: Decimal = Decimal("0.75")  # buy-YES band low edge
    favourite_hi: Decimal = Decimal("0.92")  # buy-YES band high edge (extremes above it skip)


def evaluate_favourite_longshot(
    m: Decimal,
    params: LongshotParams,
    *,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> FavouriteLongshotSignal | None:
    """Emit a favourite-longshot signal for YES price ``m``, or ``None`` outside both bands.

    - Favourite band ``[favourite_lo, favourite_hi]`` -> ``buy_yes``; score grows with ``m``.
    - Longshot band ``[longshot_lo, longshot_hi]`` -> ``buy_no``; score grows as ``m`` falls.
    - Otherwise (the dead gaps and the true extremes, where spread eats the edge) -> ``None``.
    """
    if not (ZERO <= m <= ONE):
        raise ValueError(f"price m must be in [0, 1], got {m}")

    if params.favourite_lo <= m <= params.favourite_hi:
        score = (m - params.favourite_lo) / (params.favourite_hi - params.favourite_lo)
        return FavouriteLongshotSignal(
            time=at,
            market_id=market_id,
            condition_id=condition_id,
            kind="buy_yes",
            price=m,
            edge_score=score,
        )

    if params.longshot_lo <= m <= params.longshot_hi:
        score = (params.longshot_hi - m) / (params.longshot_hi - params.longshot_lo)
        return FavouriteLongshotSignal(
            time=at,
            market_id=market_id,
            condition_id=condition_id,
            kind="buy_no",
            price=m,
            edge_score=score,
        )

    return None
