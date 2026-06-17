"""Fair-value model — time-weighted (dwell) mean midpoint + a dispersion-based lower bound.

    p_hat = Σ w_i·m_i / Σ w_i           (w_i = dwell time the midpoint m_i prevailed)
    sigma = sqrt(Σ w_i·(m_i−p_hat)² / Σ w_i)   (time-weighted stdev)
    p_lo  = clamp(p_hat − k·sigma, 0, 1)

Pure, Decimal-native, no I/O. ``p_hat`` is the smoothed consensus; ``p_lo`` is the conservative
estimate the gate/sizing consume (CLAUDE.md: size on p, gate on the lower bound).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.math.fair_value import (
    FairValueObservation,
    FairValueParams,
    estimate_fair_value,
)

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _obs(seconds: int, midpoint: str) -> FairValueObservation:
    return FairValueObservation(time=T0 + timedelta(seconds=seconds), midpoint=Decimal(midpoint))


def q(x, p="0.0001") -> Decimal:
    return Decimal(x).quantize(Decimal(p))


def test_equal_dwell_twap_and_sigma():
    # 0.40 held [0,10), 0.60 held [10,20) -> TWAP 0.50, sigma 0.10
    obs = [_obs(0, "0.40"), _obs(10, "0.60")]
    est = estimate_fair_value(obs, as_of=T0 + timedelta(seconds=20))
    assert est is not None
    assert est.p_hat == Decimal("0.50")
    assert q(est.sigma) == q("0.10")
    assert est.p_lo == Decimal("0.30")  # 0.50 - 2*0.10, default k=2
    assert est.n == 2


def test_unequal_dwell_weights_by_time():
    # 0.40 held 30s, 0.80 held 10s -> TWAP (0.4*30+0.8*10)/40 = 0.50; sigma^2 = 1.2/40 = 0.03
    obs = [_obs(0, "0.40"), _obs(30, "0.80")]
    est = estimate_fair_value(obs, as_of=T0 + timedelta(seconds=40))
    assert est.p_hat == Decimal("0.50")
    assert q(est.sigma) == q("0.1732")
    assert q(est.p_lo) == q("0.1536")  # 0.5 - 2*0.17320508


def test_constant_midpoint_has_zero_dispersion():
    obs = [_obs(0, "0.70"), _obs(5, "0.70"), _obs(12, "0.70")]
    est = estimate_fair_value(obs, as_of=T0 + timedelta(seconds=20))
    assert est.p_hat == Decimal("0.70")
    assert est.sigma == 0
    assert est.p_lo == Decimal("0.70")


def test_p_lo_clamps_at_zero():
    # low p, high dispersion would push p_lo negative -> floored at 0
    obs = [_obs(0, "0.05"), _obs(10, "0.55")]
    est = estimate_fair_value(obs, as_of=T0 + timedelta(seconds=20), params=FairValueParams(k=Decimal("5")))
    assert est.p_lo == Decimal("0")


def test_too_few_observations_returns_none():
    assert estimate_fair_value([_obs(0, "0.5")], as_of=T0 + timedelta(seconds=10)) is None
    assert estimate_fair_value([], as_of=T0) is None


def test_min_observations_is_configurable():
    obs = [_obs(0, "0.4"), _obs(10, "0.6")]
    assert estimate_fair_value(obs, as_of=T0 + timedelta(seconds=20),
                               params=FairValueParams(min_observations=3)) is None


def test_as_of_before_last_observation_raises():
    obs = [_obs(0, "0.4"), _obs(30, "0.6")]
    with pytest.raises(ValueError):
        estimate_fair_value(obs, as_of=T0 + timedelta(seconds=10))


def test_degenerate_same_timestamp_falls_back_to_simple_mean():
    # all observations at the same instant (zero total dwell) -> equal-weighted mean
    obs = [
        FairValueObservation(time=T0, midpoint=Decimal("0.40")),
        FairValueObservation(time=T0, midpoint=Decimal("0.60")),
    ]
    est = estimate_fair_value(obs, as_of=T0)
    assert est is not None
    assert est.p_hat == Decimal("0.50")
