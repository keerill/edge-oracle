"""API hardening — rate-limiter window logic + the shared-secret auth dependency."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.security import RateLimiter, cors_origins, require_api_key
from app.config import Settings


def test_rate_limiter_allows_up_to_limit_then_blocks():
    rl = RateLimiter(limit=2, window_s=60.0)
    assert rl.allow("ip1", now=0.0) is True
    assert rl.allow("ip1", now=1.0) is True
    assert rl.allow("ip1", now=2.0) is False  # 3rd within the window


def test_rate_limiter_window_slides():
    rl = RateLimiter(limit=1, window_s=60.0)
    assert rl.allow("ip1", now=0.0) is True
    assert rl.allow("ip1", now=30.0) is False  # still in window
    assert rl.allow("ip1", now=61.0) is True   # old hit expired


def test_rate_limiter_is_per_key():
    rl = RateLimiter(limit=1, window_s=60.0)
    assert rl.allow("ip1", now=0.0) is True
    assert rl.allow("ip2", now=0.0) is True  # different client, own budget


async def test_auth_open_when_no_key_configured():
    await require_api_key(x_api_key=None, settings=Settings(api_key=None))  # no raise


async def test_auth_rejects_missing_or_wrong_key():
    with pytest.raises(HTTPException) as e1:
        await require_api_key(x_api_key=None, settings=Settings(api_key="secret"))
    assert e1.value.status_code == 401
    with pytest.raises(HTTPException):
        await require_api_key(x_api_key="nope", settings=Settings(api_key="secret"))


async def test_auth_accepts_matching_key():
    await require_api_key(x_api_key="secret", settings=Settings(api_key="secret"))  # no raise


def test_cors_origins_parses_csv():
    assert cors_origins(Settings(cors_origins="https://a.app, https://b.app")) == [
        "https://a.app", "https://b.app"
    ]
    assert cors_origins(Settings(cors_origins="")) == []
