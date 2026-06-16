"""Retry/backoff tests. Async, offline, zero wall-clock (sleep + rng injected)."""

from __future__ import annotations

import httpx
import pytest

from app.polymarket.http import request_with_retry


class FakeSleep:
    """Records requested delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _sequence_client(items: list) -> httpx.AsyncClient:
    """Client whose transport yields ``items`` in order. Each item is either a
    ``(status, headers|None)`` tuple or an Exception instance to raise."""
    it = iter(items)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(it)
        if isinstance(item, Exception):
            raise item
        status, headers = item
        return httpx.Response(status, headers=headers or {}, text="ok")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _always_client(status: int, headers: dict | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, text="x")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


async def test_retries_429_then_succeeds():
    sleep = FakeSleep()
    async with _sequence_client([(429, None), (200, None)]) as client:
        resp = await request_with_retry(
            lambda: client.get("/x"),
            max_retries=5,
            base_delay=0.5,
            cap=30.0,
            jitter=True,
            sleep=sleep,
            rng=lambda: 0.0,
        )
    assert resp.status_code == 200
    # one retry; delay = base*2^0 * (0.5 + 0.5*0.0) = 0.25  (within [base/2, base])
    assert sleep.delays == [0.25]


async def test_retry_after_header_overrides_backoff():
    sleep = FakeSleep()
    async with _sequence_client([(429, {"Retry-After": "2"}), (200, None)]) as client:
        resp = await request_with_retry(
            lambda: client.get("/x"),
            max_retries=5,
            base_delay=0.5,
            cap=30.0,
            sleep=sleep,
            rng=lambda: 0.0,
        )
    assert resp.status_code == 200
    assert sleep.delays == [2.0]


async def test_5xx_retried():
    sleep = FakeSleep()
    async with _sequence_client([(503, None), (200, None)]) as client:
        resp = await request_with_retry(
            lambda: client.get("/x"),
            max_retries=5,
            base_delay=0.5,
            cap=30.0,
            sleep=sleep,
            rng=lambda: 1.0,
        )
    assert resp.status_code == 200
    assert len(sleep.delays) == 1


async def test_4xx_not_retried():
    sleep = FakeSleep()
    async with _sequence_client([(400, None)]) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(
                lambda: client.get("/x"),
                max_retries=5,
                base_delay=0.5,
                cap=30.0,
                sleep=sleep,
                rng=lambda: 0.0,
            )
    assert sleep.delays == []  # failed fast, no retries


async def test_transport_error_retried_then_succeeds():
    sleep = FakeSleep()
    async with _sequence_client([httpx.ConnectError("boom"), (200, None)]) as client:
        resp = await request_with_retry(
            lambda: client.get("/x"),
            max_retries=5,
            base_delay=0.5,
            cap=30.0,
            sleep=sleep,
            rng=lambda: 0.0,
        )
    assert resp.status_code == 200
    assert len(sleep.delays) == 1


async def test_exhaustion_raises_after_max_retries():
    sleep = FakeSleep()
    async with _always_client(429) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(
                lambda: client.get("/x"),
                max_retries=5,
                base_delay=0.1,
                cap=1.0,
                sleep=sleep,
                rng=lambda: 1.0,
            )
    # 5 retries (sleeps) then the 6th send (max_retries+1) raises
    assert len(sleep.delays) == 5
