"""Config invariants. Most of ``Settings`` is declarative (a TDD config exception); these lock
the behavioral + security-critical bits: execution is OFF by default, csv allowlists parse, and
the breaker-limit projection carries the knobs through unchanged.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings


def test_execution_is_disabled_by_default():
    # The hard CLAUDE.md gate: nothing trades unless EDGE_EXEC_ENABLED is explicitly set.
    assert Settings().enabled is False


def test_allowlists_parse_from_csv():
    s = Settings(allowlist_contracts="0xA, 0xB ,0xA", allowlist_spenders="0xS")
    assert s.allowlisted_contracts == frozenset({"0xA", "0xB"})
    assert s.allowlisted_spenders == frozenset({"0xS"})
    assert s.allowlisted_withdrawals == frozenset()


def test_breaker_limits_projection_carries_knobs():
    s = Settings(
        enabled=True,
        per_trade_cap_usd=Decimal("250"),
        max_slippage=Decimal("0.02"),
        rate_limit_count=5,
        rate_limit_notional_usd=Decimal("2000"),
        hot_wallet_cap_usd=Decimal("750"),
    )
    limits = s.breaker_limits()
    assert limits.enabled is True
    assert limits.per_trade_cap_usd == Decimal("250")
    assert limits.max_slippage == Decimal("0.02")
    assert limits.rate_limit_count == 5
    assert limits.rate_limit_notional_usd == Decimal("2000")
    assert limits.hot_wallet_cap_usd == Decimal("750")
