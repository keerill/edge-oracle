"""Periodic monitor loop — drawdown breach + calibration drift -> alerts.

A dedicated standalone CLI (mirrors ``signals/engine.py``): it owns the two *periodic* alert
conditions that need heavy DB reads + math — a backtest replay (drawdown) and the calibration
journal (drift). Those don't belong in the ingestion / signal / stream loops, which must stay
tight and latency-sensitive. The WS-drop alert is event-driven in the streaming engine instead.

``run_monitor_once`` is the injectable test seam: the backtest result and calibration records
are fetched via injected async callables, so the loop's evaluate+publish logic is unit-tested
with no DB or Redis. It RETURNS the alerts it published.

Data-gap note: there is no live equity feed or resolution-watcher yet, so drawdown runs off the
backtest replay against ``EDGE_BACKTEST_RESOLUTIONS_PATH`` and drift off the calibration journal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backtest.engine import _load_resolutions, run_backtest_once
from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store
from app.math.calibration import summarize
from app.models.alert import Alert
from app.models.backtest import BacktestResult, MarketResolution
from app.models.calibration import CalibrationRecord
from app.observability.alert_bus import publish_alert
from app.observability.alert_dedup import AlertDeduper
from app.observability.alerts import evaluate_calibration_drift, evaluate_drawdown
from app.observability.logging import configure_logging
from app.observability.metrics import start_metrics_server
from app.observability.sentry import init_sentry

logger = logging.getLogger(__name__)

BacktestFetcher = Callable[[], Awaitable[BacktestResult]]
CalibrationLoader = Callable[[], Awaitable[list[CalibrationRecord]]]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


async def run_monitor_once(
    settings: Settings,
    redis,
    *,
    fetch_backtest: BacktestFetcher,
    load_calibration_records: CalibrationLoader,
    now: Callable[[], datetime] = _utcnow,
    deduper: AlertDeduper | None = None,
) -> list[Alert]:
    """Evaluate drawdown + calibration drift once, publish any alerts, and return them.

    When a ``deduper`` is supplied (the loop owns one across cycles), a persistent condition is
    rate-limited: the same alert kind republishes only after its cooldown, and re-arms once the
    condition clears. Returns the alerts actually published this cycle."""
    detected: list[Alert] = []

    result = await fetch_backtest()
    dd_alert = evaluate_drawdown(result, settings.drawdown_alert_threshold, now=now())
    if dd_alert is not None:
        detected.append(dd_alert)

    records = await load_calibration_records()
    if records:  # an empty journal has no defined calibration -> no drift alert
        summary = summarize(records)
        drift_alert = evaluate_calibration_drift(
            summary, settings.calibration_drift_threshold, now=now()
        )
        if drift_alert is not None:
            detected.append(drift_alert)

    alerts = deduper.filter(detected, now()) if deduper is not None else detected
    for alert in alerts:
        await publish_alert(redis, settings.alerts_channel, alert)
        logger.warning(
            "alert published",
            extra={"kind": alert.kind, "severity": alert.severity, "value": str(alert.value)},
        )
    return alerts


async def run_monitor(
    settings: Settings,
    redis,
    *,
    fetch_backtest: BacktestFetcher,
    load_calibration_records: CalibrationLoader,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the monitor loop forever (or ``max_cycles`` times, for tests). Per-cycle isolation.
    Owns one ``AlertDeduper`` across cycles so a persistent condition isn't re-alerted every tick."""
    deduper = AlertDeduper(settings.alert_cooldown_s)
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        try:
            await run_monitor_once(
                settings,
                redis,
                fetch_backtest=fetch_backtest,
                load_calibration_records=load_calibration_records,
                now=now,
                deduper=deduper,
            )
        except Exception as exc:  # noqa: BLE001 - never let one cycle kill the loop
            logger.exception("monitor cycle failed: %r", exc)
        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.monitor_interval_s)


# --- real dependency wiring ----------------------------------------------------


def _resolutions(settings: Settings) -> Mapping[str, MarketResolution]:
    path = settings.backtest_resolutions_path
    if path and os.path.exists(path):
        return _load_resolutions(path)
    logger.warning("no resolutions feed; drawdown runs over a zero-bet replay")
    return {}


def _build_fetchers(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    resolutions: Mapping[str, MarketResolution],
) -> tuple[BacktestFetcher, CalibrationLoader]:
    async def fetch_backtest() -> BacktestResult:
        return await run_backtest_once(sessionmaker, settings, resolutions)

    async def load_calibration_records() -> list[CalibrationRecord]:
        async with sessionmaker() as session:
            return list(await store.load_calibration(session))

    return fetch_backtest, load_calibration_records


async def run_monitor_forever() -> None:
    """Entry point: build deps from settings, then loop."""
    import redis.asyncio as aioredis

    settings = get_settings()
    sessionmaker = get_sessionmaker()
    redis = aioredis.from_url(settings.redis_url)
    fetch_backtest, load_calibration_records = _build_fetchers(
        settings, sessionmaker, _resolutions(settings)
    )
    logger.info("monitor loop every %ss", settings.monitor_interval_s)
    try:
        await run_monitor(
            settings,
            redis,
            fetch_backtest=fetch_backtest,
            load_calibration_records=load_calibration_records,
        )
    finally:
        await redis.aclose()


async def _run_once() -> None:
    """One monitor cycle (CLI ``once`` mode, for verification)."""
    import redis.asyncio as aioredis

    settings = get_settings()
    sessionmaker = get_sessionmaker()
    redis = aioredis.from_url(settings.redis_url)
    fetch_backtest, load_calibration_records = _build_fetchers(
        settings, sessionmaker, _resolutions(settings)
    )
    try:
        alerts = await run_monitor_once(
            settings,
            redis,
            fetch_backtest=fetch_backtest,
            load_calibration_records=load_calibration_records,
        )
        logger.info("one-shot monitor: %d alert(s)", len(alerts))
    finally:
        await redis.aclose()


def main() -> None:
    """CLI: ``python -m app.monitoring.engine`` (loop) or ``... once`` (single cycle)."""
    configure_logging("quant.monitor")
    init_sentry("quant.monitor")
    settings = get_settings()
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    if mode == "once":
        asyncio.run(_run_once())
    else:
        asyncio.run(run_monitor_forever())


if __name__ == "__main__":
    main()
