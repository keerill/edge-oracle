"""connect_clob_ws invokes the injected on_drop when the connection fails.

The real socket is replaced by an injected ``connect`` whose context manager raises on enter,
and ``sleep`` is a no-op; ``max_reconnects=0`` stops after the first drop. This pins the wiring
that a dropped WS triggers the alert path (the alert itself is built by the tested
``evaluate_ws_drop`` and published by the tested ``publish_alert``).
"""

from __future__ import annotations

import pytest

from app.streaming.engine import connect_clob_ws


class _Dropper:
    """An injected connect() whose async context manager raises on enter (simulated drop)."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def __call__(self, url: str) -> _Dropper:
        return self

    async def __aenter__(self) -> object:
        raise self.exc

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


async def _noop_sleep(_delay: float) -> None:
    return None


async def test_ws_drop_invokes_on_drop_with_the_exception() -> None:
    seen: list[Exception] = []

    async def on_drop(exc: Exception) -> None:
        seen.append(exc)

    boom = ConnectionError("boom")
    agen = connect_clob_ws(
        "ws://x",
        ["t1"],
        on_drop=on_drop,
        connect=_Dropper(boom),
        sleep=_noop_sleep,
        max_reconnects=0,  # stop after the first drop
    )
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()
    assert seen == [boom]
