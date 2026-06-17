"""Fair-value model: a time-weighted (dwell) mean of the midpoint + a dispersion lower bound.

Pure, Decimal-native, no I/O. Produces the directional probability the sizing path needs:
``p_hat`` (your estimate — the smoothed market consensus) and ``p_lo`` (the conservative lower
bound the gate tests). This is the first, deliberately simple model; the calibration journal (A5)
will later tighten/replace it. ``estimate_fair_value`` returns ``None`` when there is too little
evidence (never a fabricated point estimate).

    p_hat = Σ w_i·m_i / Σ w_i
    sigma = sqrt(Σ w_i·(m_i − p_hat)² / Σ w_i)
    p_lo  = clamp(p_hat − k·sigma, 0, 1)

Weights ``w_i`` are dwell times (how long each midpoint prevailed) in whole microseconds — exact
integers, so no float enters via the clock; the only irrational step is the stdev sqrt, taken in
a fixed Decimal context. Money (midpoints) is exact Decimal throughout.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from pydantic import BaseModel, ConfigDict

ZERO = Decimal(0)
ONE = Decimal(1)
_MICRO = timedelta(microseconds=1)


class FairValueObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    midpoint: Decimal


class FairValueParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    k: Decimal = Decimal("2")  # lower-bound multiplier: p_lo = p_hat - k*sigma
    min_observations: int = 2  # need at least this many ticks for a usable estimate


class FairValueEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    p_hat: Decimal  # time-weighted mean midpoint (your probability)
    p_lo: Decimal  # conservative lower bound (the gate input), clamped to [0, 1]
    sigma: Decimal  # time-weighted standard deviation
    n: int  # number of observations used


def _sqrt(x: Decimal) -> Decimal:
    if x <= ZERO:
        return ZERO
    with localcontext() as ctx:
        ctx.prec = 50
        return +x.sqrt()


def estimate_fair_value(
    observations: Sequence[FairValueObservation],
    *,
    as_of: datetime,
    params: FairValueParams = FairValueParams(),
) -> FairValueEstimate | None:
    """Time-weighted fair value over ``observations`` (each midpoint prevails until the next,
    the last until ``as_of``). ``None`` when fewer than ``min_observations`` ticks. Raises if
    ``as_of`` precedes the last observation (the dwell window would be negative)."""
    obs = sorted(observations, key=lambda o: o.time)
    if len(obs) < params.min_observations:
        return None
    if as_of < obs[-1].time:
        raise ValueError(f"as_of {as_of} precedes the last observation {obs[-1].time}")

    # Dwell weights (microseconds): each midpoint holds until the next tick; the last until as_of.
    weights: list[Decimal] = []
    for i, o in enumerate(obs):
        end = obs[i + 1].time if i + 1 < len(obs) else as_of
        weights.append(Decimal((end - o.time) // _MICRO))

    total = sum(weights, ZERO)
    if total == ZERO:
        # Degenerate (all same instant): fall back to an equal-weighted mean.
        weights = [ONE] * len(obs)
        total = Decimal(len(obs))

    p_hat = sum((w * o.midpoint for w, o in zip(weights, obs, strict=True)), ZERO) / total
    variance = sum(
        (w * (o.midpoint - p_hat) * (o.midpoint - p_hat) for w, o in zip(weights, obs, strict=True)), ZERO
    ) / total
    sigma = _sqrt(variance)
    p_lo = p_hat - params.k * sigma
    p_lo = max(ZERO, min(ONE, p_lo))

    return FairValueEstimate(p_hat=p_hat, p_lo=p_lo, sigma=sigma, n=len(obs))
