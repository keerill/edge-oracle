"""Shared test helpers. All tests run fully offline against saved JSON fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a loader for ``tests/fixtures/<name>``."""
    return _load
