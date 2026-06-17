"""Alert de-duplication / rate-limiting for the monitor loop.

The monitor re-evaluates every cycle, so a persistent condition (drawdown still breached, model
still drifting) would re-publish the same alert every ``monitor_interval_s`` — noisy. ``AlertDeduper``
suppresses a repeat of the same alert *kind* until a cooldown elapses, and **re-arms** a kind the
moment the condition clears (a cycle with no alert of that kind), so a fresh breach alerts at once.

Pure + stateful, no I/O — the monitor owns one instance across cycles and the clock is injected.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.models.alert import Alert


class AlertDeduper:
    def __init__(self, cooldown_s: float) -> None:
        self._cooldown = timedelta(seconds=cooldown_s)
        self._last_emitted: dict[str, datetime] = {}

    def filter(self, alerts: Sequence[Alert], now: datetime) -> list[Alert]:
        """Return the subset to actually publish. A kind re-publishes only after the cooldown
        since its last emit; a kind absent this cycle is re-armed (its state is dropped)."""
        present = {a.kind for a in alerts}
        for kind in list(self._last_emitted):
            if kind not in present:  # condition cleared -> re-arm
                del self._last_emitted[kind]

        out: list[Alert] = []
        for alert in alerts:
            last = self._last_emitted.get(alert.kind)
            if last is None or (now - last) >= self._cooldown:
                out.append(alert)
                self._last_emitted[alert.kind] = now  # only advance on an actual emit
        return out
