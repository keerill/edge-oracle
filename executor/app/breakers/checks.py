"""Circuit-breaker predicates — pure functions over (intent, state, limits), no I/O.

These are the safety gates the orchestrator runs BEFORE signing; the signer re-enforces the
hard ones independently (a control that lives only here is bypassed when the executor is
compromised). Each predicate returns a rejection reason string or ``None`` (clear); ``evaluate``
aggregates them so every breached limit is reported, not just the first.

Design choices pinned by the tests:
  * caps are inclusive at the boundary (``<=``);
  * rate limiting covers count AND cumulative notional (defeats split-into-many-small bypass);
  * a single trade is bounded by BOTH the hot-float ceiling and the available balance;
  * the master switch defaults off elsewhere (``EDGE_EXEC_ENABLED``) — here ``enabled=False``
    rejects unconditionally.
``state`` counters are read from durable, signer-owned storage (not executor memory) so a
restart can't reset them — see ``app.db`` (a process-local counter would be the classic bypass).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.intent import Intent


class BreakerLimits(BaseModel):
    """The configured ceilings (sourced from ``EDGE_EXEC_*`` settings)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    per_trade_cap_usd: Decimal
    max_slippage: Decimal
    rate_limit_count: int
    rate_limit_notional_usd: Decimal
    hot_wallet_cap_usd: Decimal


class BreakerState(BaseModel):
    """Live state the predicates read: current hot balance, the rolling-window counters, and
    the allowlists. All sourced from durable storage, never in-process memory."""

    model_config = ConfigDict(frozen=True)

    hot_balance_usd: Decimal
    window_trade_count: int
    window_notional_usd: Decimal
    allowlisted_contracts: frozenset[str]
    allowlisted_spenders: frozenset[str]


class BreakerDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    approved: bool
    rejections: tuple[str, ...]


def _master_switch(_i: Intent, _s: BreakerState, limits: BreakerLimits) -> str | None:
    if not limits.enabled:
        return "execution disabled (EDGE_EXEC_ENABLED is false)"
    return None


def _per_trade_cap(intent: Intent, _s: BreakerState, limits: BreakerLimits) -> str | None:
    if intent.notional_usd > limits.per_trade_cap_usd:
        return f"per-trade cap exceeded: {intent.notional_usd} > {limits.per_trade_cap_usd}"
    return None


def _slippage_cap(intent: Intent, _s: BreakerState, limits: BreakerLimits) -> str | None:
    if intent.max_slippage > limits.max_slippage:
        return f"slippage cap exceeded: {intent.max_slippage} > {limits.max_slippage}"
    return None


def _allowlist(intent: Intent, state: BreakerState, _l: BreakerLimits) -> str | None:
    if intent.to_address not in state.allowlisted_contracts:
        return f"target contract not on allowlist: {intent.to_address}"
    if intent.action == "erc20_approve" and intent.approve_spender not in state.allowlisted_spenders:
        return f"approve spender not on allowlist: {intent.approve_spender}"
    return None


def _rate_limit(intent: Intent, state: BreakerState, limits: BreakerLimits) -> str | None:
    if state.window_trade_count + 1 > limits.rate_limit_count:
        return (
            f"rate limit exceeded: {state.window_trade_count + 1} > "
            f"{limits.rate_limit_count} trades/window"
        )
    if state.window_notional_usd + intent.notional_usd > limits.rate_limit_notional_usd:
        return (
            f"cumulative notional limit exceeded: "
            f"{state.window_notional_usd + intent.notional_usd} > {limits.rate_limit_notional_usd}/window"
        )
    return None


def _hot_wallet_cap(intent: Intent, state: BreakerState, limits: BreakerLimits) -> str | None:
    if intent.notional_usd > limits.hot_wallet_cap_usd:
        return f"hot-wallet cap exceeded: {intent.notional_usd} > {limits.hot_wallet_cap_usd}"
    if intent.notional_usd > state.hot_balance_usd:
        return f"insufficient hot balance: {intent.notional_usd} > {state.hot_balance_usd}"
    return None


_PREDICATES = (
    _master_switch,
    _per_trade_cap,
    _slippage_cap,
    _allowlist,
    _rate_limit,
    _hot_wallet_cap,
)


def evaluate(intent: Intent, state: BreakerState, limits: BreakerLimits) -> BreakerDecision:
    """Run every breaker; approve only when all clear, reporting all breaches."""
    rejections = tuple(
        reason
        for predicate in _PREDICATES
        if (reason := predicate(intent, state, limits)) is not None
    )
    return BreakerDecision(approved=not rejections, rejections=rejections)
