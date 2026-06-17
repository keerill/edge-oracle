"""Circuit-breaker predicates — pure, no I/O. Each is the safety gate the orchestrator runs
BEFORE signing (and the signer re-enforces independently). Worked examples pin every boundary.

Threat-model notes encoded here:
  * caps are inclusive at the boundary (== the limit is allowed; over is rejected);
  * rate limiting covers BOTH count and cumulative notional (stops split-into-many-small bypass);
  * allowlist checks the target contract, and the spender for erc20_approve;
  * the master switch (EDGE_EXEC_ENABLED) defaults off — nothing trades when it's false.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.breakers.checks import BreakerLimits, BreakerState, evaluate
from app.models.intent import Intent

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2026, 6, 17, 12, 5, 0, tzinfo=UTC)


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1", created_at=T0, expiry=EXP,
        source_signal_id="s1", action="clob_order", chain_id=137,
        market_id="m1", condition_id="c1", side="buy_no",
        size=Decimal("400"), max_price=Decimal("0.30"), max_slippage=Decimal("0.01"),
        notional_usd=Decimal("100"), to_address="0xExchange", token_id=None,
        approve_spender=None, approve_amount=None, nonce=7,
    )
    base.update(over)
    return Intent(**base)


def _limits(**over) -> BreakerLimits:
    base = dict(
        enabled=True,
        per_trade_cap_usd=Decimal("100"),
        max_slippage=Decimal("0.05"),
        rate_limit_count=3,
        rate_limit_notional_usd=Decimal("500"),
        hot_wallet_cap_usd=Decimal("200"),
    )
    base.update(over)
    return BreakerLimits(**base)


def _state(**over) -> BreakerState:
    base = dict(
        hot_balance_usd=Decimal("200"),
        window_trade_count=0,
        window_notional_usd=Decimal("0"),
        allowlisted_contracts=frozenset({"0xExchange"}),
        allowlisted_spenders=frozenset({"0xCTFExchange"}),
    )
    base.update(over)
    return BreakerState(**base)


# --- happy path -------------------------------------------------------------

def test_all_clear_approves_with_no_rejections():
    d = evaluate(_intent(), _state(), _limits())
    assert d.approved is True
    assert d.rejections == ()


# --- master switch ----------------------------------------------------------

def test_disabled_master_switch_rejects_everything():
    d = evaluate(_intent(), _state(), _limits(enabled=False))
    assert d.approved is False
    assert any("disabled" in r for r in d.rejections)


# --- per-trade cap (inclusive boundary) -------------------------------------

def test_per_trade_cap_allows_exactly_at_cap():
    assert evaluate(_intent(notional_usd=Decimal("100")), _state(), _limits()).approved is True


def test_per_trade_cap_rejects_above_cap():
    d = evaluate(_intent(notional_usd=Decimal("100.01")), _state(hot_balance_usd=Decimal("1000")),
                 _limits(hot_wallet_cap_usd=Decimal("1000"), rate_limit_notional_usd=Decimal("1000")))
    assert d.approved is False
    assert any("per-trade" in r for r in d.rejections)


# --- slippage cap -----------------------------------------------------------

def test_slippage_at_cap_ok_above_rejected():
    assert evaluate(_intent(max_slippage=Decimal("0.05")), _state(), _limits()).approved is True
    d = evaluate(_intent(max_slippage=Decimal("0.06")), _state(), _limits())
    assert d.approved is False and any("slippage" in r for r in d.rejections)


# --- allowlist --------------------------------------------------------------

def test_non_allowlisted_contract_is_rejected():
    d = evaluate(_intent(to_address="0xAttacker"), _state(), _limits())
    assert d.approved is False and any("allowlist" in r for r in d.rejections)


def test_erc20_approve_to_non_allowlisted_spender_is_rejected():
    intent = _intent(action="erc20_approve", side="buy_yes",
                     approve_spender="0xEvil", approve_amount=Decimal("100"))
    d = evaluate(intent, _state(), _limits())
    assert d.approved is False and any("spender" in r for r in d.rejections)


def test_erc20_approve_to_allowlisted_spender_ok():
    intent = _intent(action="erc20_approve", side="buy_yes",
                     approve_spender="0xCTFExchange", approve_amount=Decimal("100"))
    assert evaluate(intent, _state(), _limits()).approved is True


# --- rate limit: count AND cumulative notional ------------------------------

def test_rate_limit_count_inclusive_then_rejects():
    # window already has 2; this is the 3rd -> 3 <= 3 ok
    assert evaluate(_intent(), _state(window_trade_count=2), _limits()).approved is True
    # window already has 3; this would be the 4th -> reject
    d = evaluate(_intent(), _state(window_trade_count=3), _limits())
    assert d.approved is False and any("rate" in r for r in d.rejections)


def test_rate_limit_cumulative_notional_blocks_split_into_many():
    # 450 already spent in window + 100 = 550 > 500 cap -> reject even though count is fine
    d = evaluate(_intent(notional_usd=Decimal("100")),
                 _state(window_notional_usd=Decimal("450")), _limits())
    assert d.approved is False and any("cumulative" in r for r in d.rejections)


# --- hot-wallet cap: bounded by both the cap and available balance ----------

def test_hot_wallet_cap_rejects_trade_above_cap():
    d = evaluate(_intent(notional_usd=Decimal("100")), _state(),
                 _limits(per_trade_cap_usd=Decimal("1000"), hot_wallet_cap_usd=Decimal("50")))
    assert d.approved is False and any("hot" in r for r in d.rejections)


def test_hot_wallet_cap_rejects_trade_above_balance():
    d = evaluate(_intent(notional_usd=Decimal("100")),
                 _state(hot_balance_usd=Decimal("50")), _limits())
    assert d.approved is False and any("balance" in r for r in d.rejections)


# --- multiple failures aggregate -------------------------------------------

def test_multiple_failures_are_all_reported():
    d = evaluate(
        _intent(notional_usd=Decimal("999"), max_slippage=Decimal("0.9"), to_address="0xEvil"),
        _state(), _limits(),
    )
    assert d.approved is False
    assert len(d.rejections) >= 3
