"""Monitor loop — evaluates drawdown + calibration drift and publishes any alerts.

Offline: the backtest result and calibration records are supplied via injected async fetchers
(the real ``run_backtest_once`` / ``store.load_calibration`` are tested elsewhere), and a
capturing fake redis records publishes. ``run_monitor_once`` returns the alerts it published so
we can assert on them without Redis. The drift numbers mirror ``seed_demo`` (gap ≈ 0.0833).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.math.backtest import max_drawdown
from app.models.backtest import BacktestResult
from app.models.calibration import CalibrationRecord
from app.monitoring.engine import run_monitor_once

AT = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def _result(equity: list[str]) -> BacktestResult:
    series = [Decimal(x) for x in equity]
    return BacktestResult(
        initial_bankroll=series[0],
        final_bankroll=series[-1],
        total_return=(series[-1] - series[0]) / series[0],
        hit_rate=None,
        max_drawdown=max_drawdown(series),
        sharpe_like=None,
        n_bets=len(series) - 1,
        per_strategy={},
        equity_curve=(),
        closed_bets=(),
    )


def _calib(estimate: str, strategy: str, wins: int, losses: int) -> list[CalibrationRecord]:
    out: list[CalibrationRecord] = []
    for i in range(wins + losses):
        out.append(
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
    return out


def _settings() -> Settings:
    # explicit thresholds so the test never depends on env / a stray .env
    return Settings(
        drawdown_alert_threshold=Decimal("0.20"),
        calibration_drift_threshold=Decimal("0.05"),
    )


async def test_monitor_publishes_drawdown_and_drift_alerts() -> None:
    redis = _FakeRedis()
    records = _calib("0.85", "extreme_correction", 7, 3) + _calib("0.75", "favourite_longshot", 6, 2)

    async def fetch_backtest() -> BacktestResult:
        return _result(["1000", "1200", "600", "900"])  # max drawdown 0.5

    async def load_calibration_records() -> list[CalibrationRecord]:
        return records

    alerts = await run_monitor_once(
        _settings(),
        redis,
        fetch_backtest=fetch_backtest,
        load_calibration_records=load_calibration_records,
        now=lambda: AT,
    )

    assert sorted(a.kind for a in alerts) == ["calibration_drift", "drawdown_breach"]
    assert len(redis.published) == 2  # both went to the alert bus


async def test_monitor_publishes_nothing_when_healthy() -> None:
    redis = _FakeRedis()

    async def fetch_backtest() -> BacktestResult:
        return _result(["1000", "1100", "1200"])  # monotonic -> drawdown 0

    async def load_calibration_records() -> list[CalibrationRecord]:
        return _calib("0.75", "extreme_correction", 10, 0)  # underconfident -> no drift

    alerts = await run_monitor_once(
        _settings(),
        redis,
        fetch_backtest=fetch_backtest,
        load_calibration_records=load_calibration_records,
        now=lambda: AT,
    )

    assert alerts == []
    assert redis.published == []


async def test_monitor_handles_empty_calibration_journal() -> None:
    redis = _FakeRedis()

    async def fetch_backtest() -> BacktestResult:
        return _result(["1000", "1100", "1200"])  # no drawdown

    async def load_calibration_records() -> list[CalibrationRecord]:
        return []  # empty journal -> summarize not called, no drift alert

    alerts = await run_monitor_once(
        _settings(),
        redis,
        fetch_backtest=fetch_backtest,
        load_calibration_records=load_calibration_records,
        now=lambda: AT,
    )

    assert alerts == []


async def test_persistent_alert_is_deduped_across_cycles():
    """A condition that holds across cycles publishes once (cooldown), not every tick."""
    from app.observability.alert_dedup import AlertDeduper

    redis = _FakeRedis()
    s = _settings()
    breaching = _result(["1000", "700"])  # max drawdown 0.30 > 0.20 threshold

    async def fetch_backtest():
        return breaching

    async def load_calibration_records():
        return []

    deduper = AlertDeduper(cooldown_s=3600)
    a1 = await run_monitor_once(s, redis, fetch_backtest=fetch_backtest,
                                load_calibration_records=load_calibration_records,
                                now=lambda: AT, deduper=deduper)
    a2 = await run_monitor_once(s, redis, fetch_backtest=fetch_backtest,
                                load_calibration_records=load_calibration_records,
                                now=lambda: AT, deduper=deduper)
    assert [x.kind for x in a1] == ["drawdown_breach"]
    assert a2 == []  # suppressed within cooldown
    assert len(redis.published) == 1  # published only once
