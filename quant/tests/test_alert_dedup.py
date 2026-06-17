"""Alert de-dup / rate-limiting: suppress repeats within a cooldown, re-arm when a kind clears."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.alert import Alert
from app.observability.alert_dedup import AlertDeduper

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _alert(kind="drawdown_breach", value="0.3") -> Alert:
    return Alert(kind=kind, severity="error", title="t", detail="d",
                 value=Decimal(value), threshold=Decimal("0.2"), time=T0)


def test_first_occurrence_emits():
    d = AlertDeduper(cooldown_s=3600)
    assert [a.kind for a in d.filter([_alert()], T0)] == ["drawdown_breach"]


def test_repeat_within_cooldown_is_suppressed():
    d = AlertDeduper(cooldown_s=3600)
    d.filter([_alert()], T0)
    later = T0 + timedelta(minutes=30)  # < cooldown
    assert d.filter([_alert()], later) == []


def test_re_emits_after_cooldown_elapses():
    d = AlertDeduper(cooldown_s=3600)
    d.filter([_alert()], T0)
    later = T0 + timedelta(hours=2)  # > cooldown, measured from the last emit
    assert [a.kind for a in d.filter([_alert()], later)] == ["drawdown_breach"]


def test_cooldown_measured_from_last_emit_not_last_seen():
    d = AlertDeduper(cooldown_s=3600)
    d.filter([_alert()], T0)                       # emit
    d.filter([_alert()], T0 + timedelta(minutes=30))  # suppressed, must NOT reset the clock
    assert d.filter([_alert()], T0 + timedelta(minutes=50)) == []  # still < 60m from first emit


def test_cleared_condition_re_arms_immediate_emit():
    d = AlertDeduper(cooldown_s=3600)
    d.filter([_alert()], T0)                       # emit
    d.filter([], T0 + timedelta(minutes=10))       # condition cleared this cycle -> re-arm
    # reoccurs within what was the cooldown, but emits because it cleared in between
    assert [a.kind for a in d.filter([_alert()], T0 + timedelta(minutes=20))] == ["drawdown_breach"]


def test_distinct_kinds_are_independent():
    d = AlertDeduper(cooldown_s=3600)
    out = d.filter([_alert("drawdown_breach"), _alert("calibration_drift")], T0)
    assert {a.kind for a in out} == {"drawdown_breach", "calibration_drift"}
    # drift still firing, drawdown cleared -> only drawdown re-arms; drift stays suppressed
    out2 = d.filter([_alert("calibration_drift")], T0 + timedelta(minutes=5))
    assert out2 == []
