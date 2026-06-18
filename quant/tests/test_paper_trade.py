"""Offline unit tests for the PaperTrade model (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.paper_trade import PaperTrade


def _directional() -> PaperTrade:
    return PaperTrade(
        id="pt1",
        advised_at=datetime(2026, 6, 1, tzinfo=UTC),
        strategy="extreme_correction",
        market_id="m1",
        condition_id="c1",
        side="yes",
        advised_price=Decimal("0.42"),
        stake_usd=Decimal("50"),
        shares=Decimal("119.047619"),
        edge=Decimal("0.08"),
        p=Decimal("0.55"),
        p_lo=Decimal("0.50"),
    )


def test_defaults_open_and_unsettled() -> None:
    pt = _directional()
    assert pt.status == "open"
    assert pt.outcome is None
    assert pt.realized_pnl is None
    assert pt.resolved_at is None
    assert pt.signal_id is None


def test_set_arb_omits_probabilities() -> None:
    pt = PaperTrade(
        id="pt2",
        advised_at=datetime(2026, 6, 1, tzinfo=UTC),
        strategy="set_arb",
        market_id="m1",
        condition_id="c1",
        side="set",
        advised_price=Decimal("0.97"),
        stake_usd=Decimal("97"),
        shares=Decimal("100"),
        edge=Decimal("0.03"),
    )
    assert pt.p is None and pt.p_lo is None


def test_frozen() -> None:
    pt = _directional()
    with pytest.raises(ValidationError):
        pt.status = "closed"  # type: ignore[misc]


def test_rejects_unknown_side() -> None:
    with pytest.raises(ValidationError):
        PaperTrade(
            id="x",
            advised_at=datetime(2026, 6, 1, tzinfo=UTC),
            strategy="set_arb",
            market_id="m1",
            condition_id="c1",
            side="long",  # not in {yes,no,set}
            advised_price=Decimal("0.5"),
            stake_usd=Decimal("10"),
            shares=Decimal("20"),
            edge=Decimal("0.01"),
        )
