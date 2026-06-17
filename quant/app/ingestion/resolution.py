"""Pure market-resolution helpers — detect the realized outcome and journal a prediction.

NO I/O. The resolution-watcher (``resolution_engine``) uses these to turn a resolved Gamma
market + a prior estimate into a ``CalibrationRecord`` (the journal row the calibration math
scores). Only estimates that carry a probability (``extreme_correction``'s ``fair_value``) can
be journaled; risk-free arb and the probability-free longshot are skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.models.calibration import CalibrationRecord
from app.models.signal import ExtremeCorrectionSignal


def resolved_outcome(
    outcomes: Sequence[str], outcome_prices: Sequence[str]
) -> int | None:
    """The realized YES outcome of a binary Yes/No market: ``1`` if YES resolved true, ``0``
    if false, ``None`` when not a definitive Yes/No resolution (still pending, a tie, or a
    non-binary market). A resolved market has prices exactly {"1", "0"} (the winner is "1")."""
    if len(outcomes) != 2 or len(outcome_prices) != 2:
        return None
    yes_idx = next(
        (i for i, o in enumerate(outcomes) if o.strip().casefold() == "yes"), None
    )
    if yes_idx is None:
        return None
    prices = [p.strip() for p in outcome_prices]
    if {prices[0], prices[1]} != {"0", "1"}:  # not definitively resolved
        return None
    return 1 if prices[yes_idx] == "1" else 0


def calibration_from_resolution(
    signal: ExtremeCorrectionSignal, *, outcome: int, at: datetime
) -> CalibrationRecord:
    """Journal one resolved directional prediction: the probability we claimed
    (``fair_value``), the market price we saw (``price``), and the realized ``outcome``."""
    return CalibrationRecord(
        time=at,
        market_id=signal.market_id,
        condition_id=signal.condition_id,
        strategy="extreme_correction",
        estimate=signal.fair_value,
        price=signal.price,
        outcome=outcome,  # type: ignore[arg-type]  # 0/1 enforced by the caller
    )
