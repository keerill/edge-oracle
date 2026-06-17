"""Composition root for the execution pipeline — build breaker limits/state from settings.

The signer's policy + key are built by ``app.signer.deps`` (re-exported here for one import site).
The breaker *state* in this dry-run slice is a simulation stand-in: the hot float is assumed funded
to its cap and the rolling-window counters start empty (durable window enforcement off the
``exec_breaker_counters`` table is a later slice). The per-trade / hot-wallet / allowlist gates are
fully live; only the balance/window inputs are simulated.
"""

from __future__ import annotations

from decimal import Decimal

from app.breakers.checks import BreakerLimits, BreakerState
from app.config import Settings
from app.signer.deps import policy_from_settings, signer_from_settings

__all__ = ["build_limits", "build_state", "policy_from_settings", "signer_from_settings"]


def build_limits(settings: Settings) -> BreakerLimits:
    """The configured breaker ceilings (from ``EDGE_EXEC_*``)."""
    return settings.breaker_limits()


def build_state(settings: Settings) -> BreakerState:
    """The breaker's live inputs for a dry-run: allowlists from settings, hot balance assumed at
    the cap, an empty rolling window. (Real balance + durable window counters land with live exec.)"""
    return BreakerState(
        hot_balance_usd=settings.hot_wallet_cap_usd,
        window_trade_count=0,
        window_notional_usd=Decimal(0),
        allowlisted_contracts=settings.allowlisted_contracts,
        allowlisted_spenders=settings.allowlisted_spenders,
    )


def clob_exchange_address(settings: Settings) -> str:
    """The order target — the (single) allowlisted CLOB Exchange contract. Raises if the allowlist
    is empty, since an intent's ``to_address`` MUST be allowlisted (the breaker enforces it)."""
    contracts = sorted(settings.allowlisted_contracts)
    if not contracts:
        raise ValueError("EDGE_EXEC_ALLOWLIST_CONTRACTS is empty; cannot target a CLOB exchange")
    return contracts[0]
