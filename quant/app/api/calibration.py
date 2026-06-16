"""GET /calibration — how well the models' probabilities held up (Brier / log-loss /
reliability curve / shrink-only Kelly suggestion).

Returns ``null`` on an empty journal (scoring an empty set is undefined — not "calibrated").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.ingestion import store
from app.math.calibration import summarize
from app.models.calibration import CalibrationSummary

router = APIRouter(prefix="/calibration", tags=["calibration"])


@router.get("", response_model=CalibrationSummary | None)
async def get_calibration(
    session: AsyncSession = Depends(get_session),
    strategy: str | None = Query(None, description="filter the journal to one strategy tag"),
) -> CalibrationSummary | None:
    """Score the calibration journal. ``null`` when there are no resolved records yet."""
    records = await store.load_calibration(session, strategy=strategy)
    if not records:
        return None
    return summarize(records)
