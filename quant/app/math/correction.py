"""Pure price-extreme correction.

Polymarket prices at the extremes are noisier than they look (thin books, stale quotes,
resolution risk), so an extreme implied probability ``m`` is nudged a few points back
toward 0.50. The nudge is *absolute* (percentage points) and scales with how extreme the
price is: smallest (``nudge_min``) at the band edge, largest (``nudge_max``) at the most
extreme. ``fair_value`` is the corrected estimate — a future fair-value input, not a
tradeable edge by itself.

Pure: ``Decimal`` in, ``ExtremeCorrectionSignal``/``None`` out — no I/O, no clock (the
capture time is injected), no ``Settings``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.signal import ExtremeCorrectionSignal

ZERO = Decimal(0)
ONE = Decimal(1)


class CorrectionParams(BaseModel):
    """Extreme-correction knobs. Correct only when ``m < lo`` or ``m > hi`` (an *open*
    band). The nudge grows linearly with distance from ``target``, from ``nudge_min`` at
    the band edge to ``nudge_max`` at the most extreme price. All values are ``Decimal``."""

    model_config = ConfigDict(frozen=True)

    lo: Decimal = Decimal("0.15")  # correct when m < lo ...
    hi: Decimal = Decimal("0.85")  # ... or m > hi
    nudge_min: Decimal = Decimal("0.03")  # nudge at the band edge (least extreme)
    nudge_max: Decimal = Decimal("0.08")  # nudge at the most extreme (m -> 0 or 1)
    target: Decimal = Decimal("0.50")  # nudge toward this


def evaluate_extreme_correction(
    m: Decimal,
    params: CorrectionParams,
    *,
    market_id: str,
    condition_id: str,
    at: datetime,
) -> ExtremeCorrectionSignal | None:
    """Nudge an extreme price ``m`` toward ``target`` and expose the corrected estimate.

    Returns ``None`` when ``m`` is not extreme (``lo <= m <= hi``). The nudge is absolute
    and scaled by extremity (clamped to ``[nudge_min, nudge_max]``); ``fair_value`` moves
    toward ``target`` and never overshoots it for in-range inputs.
    """
    if not (ZERO <= m <= ONE):
        raise ValueError(f"price m must be in [0, 1], got {m}")
    if params.lo <= m <= params.hi:
        return None

    below = m < params.target
    if below:
        edge_d = params.target - params.lo  # distance from target at the inner band edge
        max_d = params.target - ZERO  # distance at the most extreme (m -> 0)
    else:
        edge_d = params.hi - params.target
        max_d = ONE - params.target  # distance at the most extreme (m -> 1)

    d = abs(m - params.target)
    span = max_d - edge_d
    frac = (d - edge_d) / span if span > ZERO else ONE
    frac = min(ONE, max(ZERO, frac))  # clamp to [0, 1]
    nudge = params.nudge_min + frac * (params.nudge_max - params.nudge_min)
    corrected = m + nudge if below else m - nudge

    return ExtremeCorrectionSignal(
        time=at,
        market_id=market_id,
        condition_id=condition_id,
        price=m,
        fair_value=corrected,
    )
