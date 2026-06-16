"""Shared test helpers. All tests run fully offline against saved JSON fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a loader for ``tests/fixtures/<name>``."""
    return _load


@pytest.fixture
def capturing_client() -> Callable[..., tuple[httpx.AsyncClient, dict]]:
    """Factory: build an httpx.AsyncClient backed by a MockTransport that returns
    ``response_json`` and records the outgoing request in the returned ``captured``
    dict (under key ``"request"``). The test owns the client (use ``async with``)."""

    def _make(response_json: Any, status: int = 200) -> tuple[httpx.AsyncClient, dict]:
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(status, json=response_json)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client, captured

    return _make
