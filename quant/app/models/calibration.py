"""Calibration journal models — one resolved prediction per row, plus the scored
outputs (metrics, reliability curve, Kelly-fraction adjustment).

All are frozen and ``Decimal``-native. ``CalibrationRecord`` maps 1:1 to the
``calibration`` table's NUMERIC columns (no float ever reaches the DB); the result
models are what the pure ``app.math.calibration`` functions return.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CalibrationRecord(BaseModel):
    """One resolved market: the probability we claimed (``estimate``), the market price
    we saw (``price``), and what actually happened (``outcome`` 0/1), tagged by the
    ``strategy`` that produced the estimate. ``time`` is when it was journaled (UTC)."""

    model_config = ConfigDict(frozen=True)

    time: datetime  # resolution / journal time (UTC), injected by the caller
    market_id: str
    condition_id: str
    strategy: str  # free-form producer tag (e.g. "extreme_correction"); not constrained
    estimate: Decimal = Field(ge=0, le=1)  # your claimed probability p
    price: Decimal = Field(ge=0, le=1)  # the market YES price m at the time
    outcome: Literal[0, 1]  # realized result: 1 = YES resolved true, 0 = false


class ReliabilityBin(BaseModel):
    """One decile of the reliability curve. ``claimed`` is the mean predicted probability
    of the records that fell in ``[lo, hi)`` (the last bin is closed on the right);
    ``realized`` is their observed YES frequency. Both ``None`` for an empty bin (we never
    fabricate a frequency from zero samples)."""

    model_config = ConfigDict(frozen=True)

    lo: Decimal  # bin lower edge (inclusive)
    hi: Decimal  # bin upper edge (exclusive, except the final bin)
    count: int
    claimed: Decimal | None  # mean estimate in the bin
    realized: Decimal | None  # observed outcome frequency in the bin


class CalibrationMetrics(BaseModel):
    """Scalar scores over a set of records (overall or for one strategy tag)."""

    model_config = ConfigDict(frozen=True)

    n: int
    brier: Decimal  # mean((estimate - outcome)^2), in [0, 1]
    log_loss: Decimal  # -mean(y*ln(p) + (1-y)*ln(1-p)), natural log (nats)


class KellyAdjustment(BaseModel):
    """Suggested fractional-Kelly shrink from overconfidence in the high-confidence bins.

    Shrink-only: ``adjusted_frac <= base_frac`` always. All fields except ``n_high_conf``
    are ``None`` when there are no high-confidence records (no evidence is not "calibrated").
    """

    model_config = ConfigDict(frozen=True)

    n_high_conf: int  # records with estimate >= high_confidence_threshold
    claimed_avg: Decimal | None  # mean estimate over those records
    realized_avg: Decimal | None  # observed frequency over those records
    multiplier: Decimal | None  # clamp(realized_avg / claimed_avg, min_multiplier, 1)
    adjusted_frac: Decimal | None  # base_frac * multiplier
    worst_bin_multiplier: Decimal | None  # min realized/claimed over the high-conf bins


class CalibrationSummary(BaseModel):
    """Everything the journal proves in one shot: scores overall and per strategy tag,
    the reliability curve, and the Kelly-fraction adjustment."""

    model_config = ConfigDict(frozen=True)

    overall: CalibrationMetrics
    per_strategy: dict[str, CalibrationMetrics]
    reliability: list[ReliabilityBin]
    kelly: KellyAdjustment
