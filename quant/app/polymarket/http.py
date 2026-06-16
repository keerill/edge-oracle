"""Shared async HTTP client + hand-rolled retry/backoff (no tenacity).

``request_with_retry`` is the unit-testable primitive: ``sleep`` and ``rng`` are
injected so tests run with zero wall-clock time and deterministic jitter.
``request_json`` is the thin convenience the typed clients use.

Policy: retry on 429 + 5xx and transport/timeout errors; fail fast on other 4xx.
Exponential backoff with full-ish jitter, capped; honor ``Retry-After`` when present.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings
from app.observability.metrics import HTTP_REQUEST_DURATION

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """One pooled client shared by the Gamma + CLOB clients (bases differ, so the
    clients pass absolute URLs)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_s),
        headers={"User-Agent": "edge-oracle-ingest/0.1"},
    )


def _backoff_delay(
    attempt: int, *, base: float, cap: float, jitter: bool, rng: Callable[[], float]
) -> float:
    raw = min(cap, base * (2**attempt))
    if jitter:
        # full-ish jitter in [raw/2, raw] — decorrelates concurrent retries
        return raw * (0.5 + 0.5 * rng())
    return raw


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        # HTTP-date form is rare for these endpoints; fall back to computed backoff.
        return None


def _host_of(req_or_resp: object) -> str:
    """Best-effort host label for the latency metric (never raises — it's just a label)."""
    try:
        return req_or_resp.url.host or "unknown"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return "unknown"


def _request_host(exc: BaseException) -> str:
    """Host from a transport error's request, if httpx attached one; else ``unknown``."""
    try:
        request = exc.request  # type: ignore[attr-defined]  # .request raises if unset
    except (RuntimeError, AttributeError):
        return "unknown"
    return _host_of(request)


async def request_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_retries: int,
    base_delay: float,
    cap: float,
    jitter: bool = True,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> httpx.Response:
    """Send with retries. Returns the first non-retryable response (raising via
    ``raise_for_status`` on a 4xx/5xx that we won't retry, incl. exhausted 429s)."""
    attempt = 0
    while True:
        start = time.perf_counter()
        try:
            response = await send()
        except httpx.TransportError as exc:  # includes timeouts + network errors
            HTTP_REQUEST_DURATION.labels(_request_host(exc), "error").observe(
                time.perf_counter() - start
            )
            if attempt >= max_retries:
                raise
            delay = _backoff_delay(attempt, base=base_delay, cap=cap, jitter=jitter, rng=rng)
            logger.warning(
                "transport error (%r); retry %d/%d in %.2fs", exc, attempt + 1, max_retries, delay
            )
            await sleep(delay)
            attempt += 1
            continue

        elapsed = time.perf_counter() - start
        if response.status_code in RETRYABLE_STATUSES and attempt < max_retries:
            HTTP_REQUEST_DURATION.labels(_host_of(response.request), "retry").observe(elapsed)
            delay = _retry_after_seconds(response)
            if delay is None:
                delay = _backoff_delay(attempt, base=base_delay, cap=cap, jitter=jitter, rng=rng)
            logger.warning(
                "retryable status %d; retry %d/%d in %.2fs",
                response.status_code,
                attempt + 1,
                max_retries,
                delay,
            )
            await sleep(delay)
            attempt += 1
            continue

        HTTP_REQUEST_DURATION.labels(
            _host_of(response.request), "ok" if response.is_success else "error"
        ).observe(elapsed)
        response.raise_for_status()
        return response


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    settings: Settings,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET ``url`` with the configured retry policy and return parsed JSON."""
    response = await request_with_retry(
        lambda: client.get(url, params=params),
        max_retries=settings.max_retries,
        base_delay=settings.backoff_base_s,
        cap=settings.backoff_cap_s,
        jitter=settings.backoff_jitter,
    )
    return response.json()
