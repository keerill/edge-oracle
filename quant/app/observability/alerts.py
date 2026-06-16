"""Pure alert predicates: (already-computed inputs, threshold, injected ``now``) -> Alert | None.

No I/O, no clock, no ``Settings``. They REUSE the already-tested math rather than recomputing
it: ``evaluate_drawdown`` reads ``BacktestResult.max_drawdown`` (= ``max_drawdown(equity)``) and
``evaluate_calibration_drift`` reads the ``KellyAdjustment`` on a ``CalibrationSummary``. The
drift metric is the high-confidence overconfidence gap ``claimed_avg - realized_avg`` — exactly
the divergence that shrinks Kelly, so an alert means "the model is more confident than reality".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models.alert import Alert
from app.models.backtest import BacktestResult
from app.models.calibration import CalibrationSummary


def evaluate_ws_drop(*, drops: int, window_drops_threshold: int, now: datetime) -> Alert | None:
    """Fire when the reconnect count meets/exceeds the threshold (the caller owns the window)."""
    if drops < window_drops_threshold:
        return None
    return Alert(
        kind="ws_drop",
        severity="warning",
        title="Polymarket WS dropped",
        detail=f"{drops} reconnect(s) at/over threshold {window_drops_threshold}",
        value=Decimal(drops),
        threshold=Decimal(window_drops_threshold),
        time=now,
    )


def evaluate_drawdown(
    result: BacktestResult, threshold: Decimal, *, now: datetime
) -> Alert | None:
    """Fire when realized max drawdown (already computed on ``result``) is >= ``threshold``."""
    dd = result.max_drawdown
    if dd < threshold:
        return None
    return Alert(
        kind="drawdown_breach",
        severity="error",
        title="Drawdown threshold breached",
        detail=f"max drawdown {dd} >= threshold {threshold}",
        value=dd,
        threshold=threshold,
        time=now,
    )


def evaluate_calibration_drift(
    summary: CalibrationSummary, threshold: Decimal, *, now: datetime
) -> Alert | None:
    """Fire when the high-confidence claimed-vs-realized gap (overconfidence) is >= ``threshold``.

    Returns ``None`` when there is no high-confidence evidence (``claimed_avg is None`` — no
    evidence is not drift) or when the gap is below the threshold (including underconfidence,
    where ``realized > claimed`` gives a negative gap).
    """
    kelly = summary.kelly
    if kelly.claimed_avg is None or kelly.realized_avg is None:
        return None
    drift = kelly.claimed_avg - kelly.realized_avg
    if drift < threshold:
        return None
    return Alert(
        kind="calibration_drift",
        severity="warning",
        title="Calibration drift",
        detail=(
            f"high-confidence claimed {kelly.claimed_avg} vs realized {kelly.realized_avg} "
            f"(gap {drift} >= threshold {threshold})"
        ),
        value=drift,
        threshold=threshold,
        time=now,
    )
