"""JSON log formatter — the structured-logging boundary.

The formatter is pure (LogRecord -> str), so it's unit-tested directly. We assert the
five base fields are always present, that ``extra=`` kwargs ride along, and that an
exception is rendered into an ``exc`` field (so Sentry/log scrapers see the traceback).
"""

from __future__ import annotations

import json
import logging
import sys

from app.observability.logging import JsonFormatter


def _record(msg: str, *args: object, **kwargs: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=args,
        exc_info=None,
        **kwargs,  # type: ignore[arg-type]
    )


def test_formats_the_five_base_fields() -> None:
    out = json.loads(JsonFormatter("quant.test").format(_record("hello %s", "world")))
    assert out["level"] == "INFO"
    assert out["logger"] == "app.test"
    assert out["msg"] == "hello world"  # args interpolated
    assert out["service"] == "quant.test"
    assert out["ts"].endswith("+00:00")  # ISO-8601 UTC


def test_includes_extra_fields() -> None:
    record = _record("scan complete")
    record.market_id = "m1"  # what logging does for `extra={"market_id": "m1"}`
    record.quotes = 10
    out = json.loads(JsonFormatter("quant.test").format(record))
    assert out["market_id"] == "m1"
    assert out["quotes"] == 10


def test_renders_exception_into_exc_field() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=20,
            msg="it failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    out = json.loads(JsonFormatter("quant.test").format(record))
    assert out["level"] == "ERROR"
    assert "ValueError: boom" in out["exc"]


def test_output_is_one_line() -> None:
    # One JSON object per line — multi-line tracebacks must not break log parsing.
    try:
        raise RuntimeError("multi\nline")
    except RuntimeError:
        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=30,
            msg="oops",
            args=(),
            exc_info=sys.exc_info(),
        )
    line = JsonFormatter("quant.test").format(record)
    assert "\n" not in line
    json.loads(line)  # still valid JSON
