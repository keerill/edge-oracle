"""Alert predicates — pure, offline, exact Decimal, with worked numeric examples.

These decide WHEN to alert; they reuse the already-tested math (``max_drawdown`` and the
calibration ``summarize``/``KellyAdjustment``) rather than recomputing it. Written before the
predicates exist and before any wiring, per CLAUDE.md (every decision fn is tested first).

The calibration-drift numbers mirror ``seed_demo``'s overconfident journal so the dev
verification path actually fires.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from app.math.backtest import max_drawdown
from app.math.calibration import summarize
from app.models.backtest import BacktestResult
from app.models.calibration import CalibrationRecord
from app.observability.alerts import (
    evaluate_calibration_drift,
    evaluate_drawdown,
    evaluate_ws_drop,
)

AT = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


# --- helpers -----------------------------------------------------------------


def _calib_records(estimate: str, strategy: str, wins: int, losses: int) -> list[CalibrationRecord]:
    recs: list[CalibrationRecord] = []
    for i in range(wins + losses):
        recs.append(
            CalibrationRecord(
                time=AT,
                market_id=f"{strategy}-{i}",
                condition_id=f"c-{strategy}-{i}",
                strategy=strategy,
                estimate=Decimal(estimate),
                price=Decimal("0.5"),
                outcome=1 if i < wins else 0,
            )
        )
    return recs


def _result(equity: list[str]) -> BacktestResult:
    series = [Decimal(x) for x in equity]
    return BacktestResult(
        initial_bankroll=series[0],
        final_bankroll=series[-1],
        total_return=(series[-1] - series[0]) / series[0],
        hit_rate=None,
        max_drawdown=max_drawdown(series),  # reuse the real, tested function
        sharpe_like=None,
        n_bets=len(series) - 1,
        per_strategy={},
        equity_curve=(),
        closed_bets=(),
    )


# --- ws drop -----------------------------------------------------------------


def test_ws_drop_fires_at_threshold() -> None:
    alert = evaluate_ws_drop(drops=1, window_drops_threshold=1, now=AT)
    assert alert is not None
    assert alert.kind == "ws_drop"
    assert alert.severity == "warning"
    assert alert.value == Decimal(1)
    assert alert.threshold == Decimal(1)
    assert alert.time == AT


def test_ws_drop_below_threshold_is_none() -> None:
    assert evaluate_ws_drop(drops=0, window_drops_threshold=1, now=AT) is None


# --- drawdown ----------------------------------------------------------------


def test_drawdown_worked_example_fires() -> None:
    result = _result(["1000", "1200", "600", "900"])
    assert result.max_drawdown == Decimal("0.5")  # (1200-600)/1200, exact
    alert = evaluate_drawdown(result, Decimal("0.20"), now=AT)
    assert alert is not None
    assert alert.kind == "drawdown_breach"
    assert alert.severity == "error"
    assert alert.value == Decimal("0.5")
    assert alert.threshold == Decimal("0.20")


def test_drawdown_below_threshold_is_none() -> None:
    result = _result(["1000", "1200", "600", "900"])
    assert evaluate_drawdown(result, Decimal("0.60"), now=AT) is None


def test_drawdown_monotonic_never_fires() -> None:
    result = _result(["1000", "1100", "1200"])
    assert result.max_drawdown == Decimal(0)
    assert evaluate_drawdown(result, Decimal("0.20"), now=AT) is None


# --- calibration drift -------------------------------------------------------


def _overconfident_summary():
    # high-confidence set (estimate >= 0.7): 10 @0.85 (7 win) + 8 @0.75 (6 win)
    # claimed = (10*0.85 + 8*0.75)/18 = 14.5/18 ; realized = 13/18 ; gap = 1.5/18 ≈ 0.0833
    records = _calib_records("0.85", "extreme_correction", wins=7, losses=3)
    records += _calib_records("0.75", "favourite_longshot", wins=6, losses=2)
    return summarize(records)


def test_calibration_drift_overconfident_fires() -> None:
    summary = _overconfident_summary()
    expected = summary.kelly.claimed_avg - summary.kelly.realized_avg
    assert expected.quantize(Decimal("0.0001")) == Decimal("0.0833")  # sanity on the gap
    alert = evaluate_calibration_drift(summary, Decimal("0.05"), now=AT)
    assert alert is not None
    assert alert.kind == "calibration_drift"
    assert alert.severity == "warning"
    assert alert.value == expected  # exact: same Decimal expression the predicate uses
    assert alert.threshold == Decimal("0.05")


def test_calibration_drift_below_threshold_is_none() -> None:
    summary = _overconfident_summary()
    # gap ≈ 0.0833 < 0.10 -> no alert
    assert evaluate_calibration_drift(summary, Decimal("0.10"), now=AT) is None


def test_calibration_drift_underconfident_is_none() -> None:
    # realized (all win) > claimed -> negative gap -> never fires
    summary = summarize(_calib_records("0.75", "extreme_correction", wins=10, losses=0))
    assert evaluate_calibration_drift(summary, Decimal("0.05"), now=AT) is None


def test_calibration_drift_no_high_confidence_is_none() -> None:
    # all estimates below the 0.7 high-confidence threshold -> kelly diagnostics are None
    summary = summarize(_calib_records("0.60", "extreme_correction", wins=5, losses=5))
    assert summary.kelly.claimed_avg is None
    assert evaluate_calibration_drift(summary, Decimal("0.05"), now=AT) is None


# --- wire contract -----------------------------------------------------------


def test_alert_serializes_decimal_value_as_string() -> None:
    alert = evaluate_drawdown(_result(["1000", "1200", "600", "900"]), Decimal("0.20"), now=AT)
    assert alert is not None
    payload = json.loads(alert.model_dump_json())
    assert payload["value"] == "0.5"  # Decimal -> JSON string, same contract as AdvisedSignal
    assert isinstance(payload["value"], str)
