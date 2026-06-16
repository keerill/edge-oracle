"""Calibration scoring — pure metrics over a resolved-prediction journal.

``Decimal`` in, frozen models out; no I/O, no clock, no ``Settings``. Brier is exact
arithmetic. Log-loss uses ``Decimal.ln()`` (natural log) inside a fixed-precision local
context so the result is reproducible regardless of the caller's decimal context, with
probabilities clipped to ``[eps, 1-eps]`` to keep ``ln`` finite at the extremes. The
Kelly-fraction adjustment is shrink-only: it can lower the fractional-Kelly value when the
model is overconfident in its high-confidence predictions, never raise it.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

from pydantic import BaseModel, ConfigDict, Field

from app.models.calibration import (
    CalibrationMetrics,
    CalibrationRecord,
    CalibrationSummary,
    KellyAdjustment,
    ReliabilityBin,
)

ZERO = Decimal(0)
ONE = Decimal(1)


class CalibrationParams(BaseModel):
    """Knobs for the calibration math (frozen, ``Decimal``-native)."""

    model_config = ConfigDict(frozen=True)

    n_bins: int = Field(default=10, ge=1)
    # log-loss clip; keeps ln finite at p in {0, 1}
    eps: Decimal = Field(default=Decimal("1e-12"), gt=0, lt=Decimal("0.5"))
    # > 0 so claimed_avg over the high-confidence set is > 0 (the Kelly divide is safe)
    high_confidence_threshold: Decimal = Field(default=Decimal("0.7"), gt=0, le=1)
    base_frac: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)  # default fractional Kelly
    min_multiplier: Decimal = Field(default=Decimal("0"), ge=0, le=1)  # floor on the shrink factor
    ln_prec: int = Field(default=50, ge=1)  # fixed precision for the log-loss natural logs


def brier_score(records: Sequence[CalibrationRecord]) -> Decimal:
    """Mean squared error ``mean((estimate - outcome)^2)`` — single-class binary, in
    ``[0, 1]``, exact in ``Decimal``. Raises ``ValueError`` on an empty journal."""
    if not records:
        raise ValueError("brier_score requires at least one record")
    total = sum(((r.estimate - r.outcome) ** 2 for r in records), ZERO)
    return total / Decimal(len(records))


def log_loss(
    records: Sequence[CalibrationRecord],
    eps: Decimal = Decimal("1e-12"),
    prec: int = 50,
) -> Decimal:
    """Mean negative log-likelihood ``-mean(y*ln(p) + (1-y)*ln(1-p))`` in nats. ``p`` is
    clipped once to ``[eps, 1-eps]`` (the same clipped value feeds both terms) so ``ln``
    never hits 0. Computed at fixed precision ``prec`` so the result does not depend on the
    caller's context. Raises ``ValueError`` on an empty journal."""
    if not records:
        raise ValueError("log_loss requires at least one record")
    hi = ONE - eps
    with localcontext() as ctx:
        ctx.prec = prec
        total = ZERO
        for r in records:
            cp = min(max(r.estimate, eps), hi)  # clip once
            total += cp.ln() if r.outcome == 1 else (ONE - cp).ln()
        return -total / Decimal(len(records))


def reliability_curve(
    records: Sequence[CalibrationRecord], n_bins: int = 10
) -> list[ReliabilityBin]:
    """Bin predictions into ``n_bins`` equal-width buckets over ``[0, 1]`` and report, per
    bin, the mean claimed probability vs the realized outcome frequency. All ``n_bins``
    bins are returned; an empty bin has ``count=0`` and ``claimed=realized=None`` (a
    frequency is never fabricated from zero samples)."""
    counts, sum_estimate, sum_outcome = _bin_records(records, n_bins)
    n = Decimal(n_bins)
    bins: list[ReliabilityBin] = []
    for i in range(n_bins):
        c = counts[i]
        bins.append(
            ReliabilityBin(
                lo=Decimal(i) / n,
                hi=Decimal(i + 1) / n,
                count=c,
                claimed=(sum_estimate[i] / Decimal(c)) if c else None,
                realized=(Decimal(sum_outcome[i]) / Decimal(c)) if c else None,
            )
        )
    return bins


def suggest_kelly_fraction(
    records: Sequence[CalibrationRecord], params: CalibrationParams | None = None
) -> KellyAdjustment:
    """Propose a (shrink-only) fractional-Kelly value from overconfidence in the
    high-confidence predictions (``estimate >= high_confidence_threshold``).

    ``multiplier = clamp(realized_avg / claimed_avg, min_multiplier, 1)`` so
    ``adjusted_frac = base_frac * multiplier <= base_frac`` always — underconfidence
    (``realized > claimed``) clamps to 1 and leaves the fraction unchanged; it can never
    *raise* risk. With no high-confidence records every diagnostic is ``None`` — no
    evidence is not the same as "calibrated"."""
    params = params or CalibrationParams()
    hi_conf = [r for r in records if r.estimate >= params.high_confidence_threshold]
    if not hi_conf:
        return KellyAdjustment(
            n_high_conf=0,
            claimed_avg=None,
            realized_avg=None,
            multiplier=None,
            adjusted_frac=None,
            worst_bin_multiplier=None,
        )
    n = Decimal(len(hi_conf))
    claimed_avg = sum((r.estimate for r in hi_conf), ZERO) / n
    realized_avg = Decimal(sum(r.outcome for r in hi_conf)) / n
    multiplier = _clamp(realized_avg / claimed_avg, params.min_multiplier, ONE)
    return KellyAdjustment(
        n_high_conf=len(hi_conf),
        claimed_avg=claimed_avg,
        realized_avg=realized_avg,
        multiplier=multiplier,
        adjusted_frac=params.base_frac * multiplier,
        worst_bin_multiplier=_worst_bin_multiplier(hi_conf, params),
    )


def summarize(
    records: Sequence[CalibrationRecord], params: CalibrationParams | None = None
) -> CalibrationSummary:
    """Score the whole journal: Brier + log-loss overall and per strategy tag (both
    pooled over their records), the reliability curve, and the Kelly-fraction adjustment."""
    params = params or CalibrationParams()
    per_strategy = {
        tag: _metrics([r for r in records if r.strategy == tag], params)
        for tag in dict.fromkeys(r.strategy for r in records)
    }
    return CalibrationSummary(
        overall=_metrics(records, params),
        per_strategy=per_strategy,
        reliability=reliability_curve(records, params.n_bins),
        kelly=suggest_kelly_fraction(records, params),
    )


# --- internal helpers ----------------------------------------------------------

def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(x, hi))


def _bin_records(
    records: Sequence[CalibrationRecord], n_bins: int
) -> tuple[list[int], list[Decimal], list[int]]:
    """Tally count / Σestimate / Σoutcome per equal-width decile. ``estimate`` stays
    ``Decimal`` throughout; ``estimate == 1.0`` falls into the final (closed) bin."""
    counts = [0] * n_bins
    sum_estimate: list[Decimal] = [ZERO] * n_bins
    sum_outcome = [0] * n_bins
    for r in records:
        idx = int(r.estimate * n_bins)
        if idx >= n_bins:
            idx = n_bins - 1
        counts[idx] += 1
        sum_estimate[idx] += r.estimate
        sum_outcome[idx] += r.outcome
    return counts, sum_estimate, sum_outcome


def _metrics(
    records: Sequence[CalibrationRecord], params: CalibrationParams
) -> CalibrationMetrics:
    return CalibrationMetrics(
        n=len(records),
        brier=brier_score(records),
        log_loss=log_loss(records, params.eps, params.ln_prec),
    )


def _worst_bin_multiplier(
    hi_conf: Sequence[CalibrationRecord], params: CalibrationParams
) -> Decimal:
    """The most overconfident single high-confidence bin's shrink factor (a diagnostic;
    the headline multiplier uses the pooled average instead). Claimed ≥ the threshold > 0
    in every populated bin, so the divide is safe."""
    counts, sum_estimate, sum_outcome = _bin_records(hi_conf, params.n_bins)
    worst = ONE
    for i in range(params.n_bins):
        if not counts[i]:
            continue
        claimed = sum_estimate[i] / Decimal(counts[i])
        realized = Decimal(sum_outcome[i]) / Decimal(counts[i])
        worst = min(worst, _clamp(realized / claimed, params.min_multiplier, ONE))
    return worst
