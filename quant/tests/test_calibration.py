"""Worked-example tests for the calibration math (offline, exact Decimal).

Every metric is pinned to a hand-computable number. Brier terminates exactly so we use
``==``; log-loss is irrational (natural log) so we ``quantize`` to 6 dp against a known
constant. No ``pytest.approx`` / ``math.isclose`` — same rule as the rest of the suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.math.calibration import (
    CalibrationParams,
    brier_score,
    calibration_timeline,
    log_loss,
    reliability_curve,
    suggest_kelly_fraction,
    summarize,
)
from app.models.calibration import CalibrationRecord, CalibrationTimePoint, KellyAdjustment

Q6 = Decimal("0.000001")
_T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _rec(estimate: str, outcome: int, strategy: str = "s", m: str = "0.5") -> CalibrationRecord:
    """A calibration record with the math-irrelevant fields filled by fixed defaults."""
    return CalibrationRecord(
        time=_T,
        market_id="m1",
        condition_id="c1",
        strategy=strategy,
        estimate=Decimal(estimate),
        price=Decimal(m),
        outcome=outcome,
    )


def _rec_at(day: int, estimate: str, outcome: int, strategy: str = "s") -> CalibrationRecord:
    """A calibration record stamped on ``2026-01-<day>`` (for the cumulative timeline)."""
    return CalibrationRecord(
        time=datetime(2026, 1, day, tzinfo=timezone.utc),
        market_id="m1",
        condition_id="c1",
        strategy=strategy,
        estimate=Decimal(estimate),
        price=Decimal("0.5"),
        outcome=outcome,
    )


# --- Brier score ---------------------------------------------------------------

def test_brier_all_half_is_quarter():
    # p=0.5 always: every term is (0.5 - y)^2 = 0.25 regardless of outcome -> mean 0.25.
    recs = [_rec("0.5", 1), _rec("0.5", 0)]
    b = brier_score(recs)
    assert b == Decimal("0.25")
    assert isinstance(b, Decimal)


def test_brier_worked_example_terminates():
    # estimates [0.9,0.9,0.1,0.1], outcomes [1,0,0,1]:
    # 0.01 + 0.81 + 0.01 + 0.81 = 1.64; /4 = 0.41 exactly.
    recs = [_rec("0.9", 1), _rec("0.9", 0), _rec("0.1", 0), _rec("0.1", 1)]
    assert brier_score(recs) == Decimal("0.41")


def test_brier_empty_raises():
    with pytest.raises(ValueError):
        brier_score([])


# --- Log-loss ------------------------------------------------------------------

def test_log_loss_all_half_is_ln2():
    # p=0.5 always: -mean(ln(0.5)) = ln(2) = 0.6931471805599453...
    recs = [_rec("0.5", 1), _rec("0.5", 0)]
    assert log_loss(recs).quantize(Q6) == Decimal("0.693147")


def test_log_loss_worked_example():
    # Same set as Brier: sum = -2*ln(0.09); mean = -ln(0.09)/2 = 1.2039728043...
    recs = [_rec("0.9", 1), _rec("0.9", 0), _rec("0.1", 0), _rec("0.1", 1)]
    assert log_loss(recs).quantize(Q6) == Decimal("1.203973")


def test_log_loss_clips_zero_probability():
    # p=0, outcome=1 would be -ln(0) = +inf; clip to eps=1e-12 -> -ln(1e-12) = 12*ln(10).
    recs = [_rec("0", 1)]
    assert log_loss(recs).quantize(Q6) == Decimal("27.631021")


def test_log_loss_empty_raises():
    with pytest.raises(ValueError):
        log_loss([])


# --- Reliability curve ---------------------------------------------------------

def test_reliability_curve_claimed_vs_realized():
    # Bin 9 [0.9,1.0]: ten at 0.95, eight YES -> claimed 0.95, realized 0.8.
    # Bin 1 [0.1,0.2): ten at 0.15, two YES   -> claimed 0.15, realized 0.2.
    recs = (
        [_rec("0.95", 1) for _ in range(8)]
        + [_rec("0.95", 0) for _ in range(2)]
        + [_rec("0.15", 1) for _ in range(2)]
        + [_rec("0.15", 0) for _ in range(8)]
    )
    bins = reliability_curve(recs)
    assert len(bins) == 10

    assert bins[9].count == 10
    assert bins[9].lo == Decimal("0.9")
    assert bins[9].hi == Decimal("1")
    assert bins[9].claimed == Decimal("0.95")
    assert bins[9].realized == Decimal("0.8")

    assert bins[1].count == 10
    assert bins[1].claimed == Decimal("0.15")
    assert bins[1].realized == Decimal("0.2")

    # untouched bins are empty, never fabricated
    assert bins[5].count == 0
    assert bins[5].claimed is None
    assert bins[5].realized is None


def test_reliability_curve_bin_edges():
    # 0.0 -> bin 0; 0.7 lands exactly on the bin-7 left edge; 1.0 -> last bin (closed).
    recs = [_rec("0.0", 0), _rec("0.7", 1), _rec("1.0", 1)]
    bins = reliability_curve(recs)
    assert bins[0].count == 1
    assert bins[7].count == 1
    assert bins[7].lo == Decimal("0.7")
    assert bins[7].hi == Decimal("0.8")
    assert bins[9].count == 1


# --- Kelly-fraction adjustment -------------------------------------------------

def test_kelly_shrinks_on_overconfidence():
    # Ten at p=0.8 (all high-conf, all bin 8), six YES -> realized 0.6, claimed 0.8.
    # multiplier = 0.6/0.8 = 0.75; adjusted_frac = 0.25 * 0.75 = 0.1875.
    recs = [_rec("0.8", 1) for _ in range(6)] + [_rec("0.8", 0) for _ in range(4)]
    adj = suggest_kelly_fraction(recs)
    assert adj.n_high_conf == 10
    assert adj.claimed_avg == Decimal("0.8")
    assert adj.realized_avg == Decimal("0.6")
    assert adj.multiplier == Decimal("0.75")
    assert adj.adjusted_frac == Decimal("0.1875")
    assert adj.worst_bin_multiplier == Decimal("0.75")
    assert adj.adjusted_frac <= CalibrationParams().base_frac


def test_kelly_no_increase_when_underconfident():
    # realized 0.9 > claimed 0.8 -> multiplier clamps to 1 -> frac unchanged at base 0.25.
    recs = [_rec("0.8", 1) for _ in range(9)] + [_rec("0.8", 0)]
    adj = suggest_kelly_fraction(recs)
    assert adj.multiplier == Decimal("1")
    assert adj.adjusted_frac == Decimal("0.25")
    assert adj.adjusted_frac <= CalibrationParams().base_frac


def test_kelly_total_shrink_when_never_realized():
    # Ten at p=0.9, zero YES -> realized 0; multiplier floors at 0 -> frac 0.
    recs = [_rec("0.9", 0) for _ in range(10)]
    adj = suggest_kelly_fraction(recs)
    assert adj.realized_avg == Decimal("0")
    assert adj.multiplier == Decimal("0")
    assert adj.adjusted_frac == Decimal("0")


def test_kelly_worst_bin_below_aggregate():
    # Bin 7 (p=0.7) well-calibrated -> clamps to 1; bin 8 (p=0.8) overconfident -> 0.75.
    # Aggregate is milder than the worst single bin.
    recs = (
        [_rec("0.7", 1) for _ in range(8)] + [_rec("0.7", 0) for _ in range(2)]  # realized 0.8
        + [_rec("0.8", 1) for _ in range(6)] + [_rec("0.8", 0) for _ in range(4)]  # realized 0.6
    )
    adj = suggest_kelly_fraction(recs)
    assert adj.n_high_conf == 20
    assert adj.worst_bin_multiplier == Decimal("0.75")  # the overconfident bin
    # aggregate: claimed_avg 0.75, realized_avg 0.7 -> 0.7/0.75 = 0.9333...
    assert adj.multiplier.quantize(Q6) == Decimal("0.933333")
    assert adj.adjusted_frac.quantize(Q6) == Decimal("0.233333")
    assert adj.worst_bin_multiplier < adj.multiplier


def test_kelly_no_high_confidence_is_none_not_zero():
    # No record >= 0.7: no evidence, not "calibrated" -> diagnostics are None.
    recs = [_rec("0.5", 1), _rec("0.3", 0)]
    adj = suggest_kelly_fraction(recs)
    assert isinstance(adj, KellyAdjustment)
    assert adj.n_high_conf == 0
    assert adj.claimed_avg is None
    assert adj.realized_avg is None
    assert adj.multiplier is None
    assert adj.adjusted_frac is None
    assert adj.worst_bin_multiplier is None


# --- Summary (overall + per strategy, pooled) ----------------------------------

def test_summary_per_strategy_is_pooled_not_mean_of_means():
    # Strategy A: 3 records at p=0.5 -> Brier 0.25. Strategy B: 1 perfect record -> Brier 0.
    # Pooled overall = (0.25*3 + 0)/4 = 0.1875; mean-of-means would be 0.125 (wrong).
    recs = [
        _rec("0.5", 1, strategy="A"),
        _rec("0.5", 0, strategy="A"),
        _rec("0.5", 1, strategy="A"),
        _rec("0.0", 0, strategy="B"),
    ]
    summ = summarize(recs)

    assert summ.overall.n == 4
    assert summ.overall.brier == Decimal("0.1875")

    assert summ.per_strategy["A"].n == 3
    assert summ.per_strategy["A"].brier == Decimal("0.25")
    assert summ.per_strategy["B"].n == 1
    assert summ.per_strategy["B"].brier == Decimal("0")

    # the curve and the adjustment ride along
    assert len(summ.reliability) == 10
    assert isinstance(summ.kelly, KellyAdjustment)


# --- Cumulative timeline (Brier / log-loss over the journal's time axis) --------

def test_calibration_timeline_empty_is_empty():
    # Scoring an empty prefix is undefined; the timeline of nothing is the empty list.
    assert calibration_timeline([]) == []


def test_calibration_timeline_cumulative_through_distinct_times():
    # day1: two p=0.5 (one YES, one NO) -> cumulative Brier 0.25, log-loss ln(2).
    # day2: + one perfect p=0.0/outcome 0 -> cumulative Brier (0.25*2 + 0)/3 = 0.5/3,
    #   log-loss -(ln 0.5 + ln 0.5 + ln(1-eps))/3 = 2*ln(2)/3 = 0.4620981...
    recs = [_rec_at(1, "0.5", 1), _rec_at(1, "0.5", 0), _rec_at(2, "0.0", 0)]
    tl = calibration_timeline(recs)

    assert len(tl) == 2  # one point per distinct timestamp
    assert isinstance(tl[0], CalibrationTimePoint)

    assert tl[0].n == 2
    assert tl[0].brier == Decimal("0.25")
    assert tl[0].log_loss.quantize(Q6) == Decimal("0.693147")

    assert tl[1].n == 3
    assert tl[1].brier.quantize(Q6) == Decimal("0.166667")
    assert tl[1].log_loss.quantize(Q6) == Decimal("0.462098")


def test_calibration_timeline_sorts_and_final_point_equals_overall():
    # Given out of time order: points come out ascending in time, and the final cumulative
    # point covers every record -> its scores are exactly the overall metrics.
    recs = [_rec_at(2, "0.0", 0), _rec_at(1, "0.5", 1), _rec_at(1, "0.5", 0)]
    tl = calibration_timeline(recs)
    summ = summarize(recs)

    assert [p.time for p in tl] == sorted(p.time for p in tl)
    assert tl[-1].n == summ.overall.n == 3
    assert tl[-1].brier == summ.overall.brier  # order-independent -> exact equality
    assert tl[-1].log_loss == summ.overall.log_loss


def test_summary_includes_timeline():
    recs = [_rec_at(1, "0.5", 1), _rec_at(2, "0.5", 0)]
    summ = summarize(recs)
    assert isinstance(summ.timeline, list)
    assert len(summ.timeline) == 2
    assert summ.timeline[-1].n == 2
