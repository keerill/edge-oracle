"""Signer-side default-deny policy — the single most important control in the threat model.

The signer assumes the executor host is compromised, so it re-validates every intent against its
OWN policy before signing anything: intent-hash integrity, chain/expiry, an action allowlist,
contract + spender allowlists, per-tx + slippage caps, exact (never infinite) allowances, and a
required approval token above the threshold. Anything it can't fully vouch for is DENIED.

Pure, no crypto, no I/O — these worked examples pin every deny path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.intent import Intent, IntentEnvelope
from app.signer.policy import SignerPolicy, evaluate_policy

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = NOW + timedelta(minutes=5)


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1", created_at=NOW, expiry=EXP, source_signal_id="s1",
        action="clob_order", chain_id=137, market_id="m1", condition_id="c1",
        side="buy_no", size=Decimal("400"), max_price=Decimal("0.30"),
        max_slippage=Decimal("0.01"), notional_usd=Decimal("100"),
        to_address="0xExchange", token_id=None, approve_spender=None,
        approve_amount=None, nonce=0,
    )
    base.update(over)
    return Intent(**base)


def _policy(**over) -> SignerPolicy:
    base = dict(
        chain_id=137,
        allowed_actions=frozenset({"clob_order", "ctf_split", "ctf_merge", "ctf_redeem", "erc20_approve"}),
        allowlisted_contracts=frozenset({"0xExchange", "0xCTF"}),
        allowlisted_spenders=frozenset({"0xExchange"}),
        max_notional_usd=Decimal("100"),
        max_slippage=Decimal("0.05"),
        approval_threshold_usd=Decimal("50"),
    )
    base.update(over)
    return SignerPolicy(**base)


def _evaluate(intent, *, policy=None, now=NOW, approval_valid=False):
    envelope = IntentEnvelope.seal(intent)
    return evaluate_policy(envelope, policy or _policy(), now=now, approval_valid=approval_valid)


def test_clean_intent_below_threshold_is_allowed():
    v = _evaluate(_intent(notional_usd=Decimal("40")))  # < 50 threshold -> no approval needed
    assert v.allowed is True
    assert v.reasons == ()


def test_tampered_intent_hash_is_denied():
    good = IntentEnvelope.seal(_intent())
    doctored = IntentEnvelope(intent=_intent(to_address="0xAttacker"), intent_hash=good.intent_hash)
    v = evaluate_policy(doctored, _policy(), now=NOW, approval_valid=True)
    assert v.allowed is False and any("hash" in r for r in v.reasons)


def test_wrong_chain_is_denied():
    v = _evaluate(_intent(chain_id=1), approval_valid=True)
    assert v.allowed is False and any("chain" in r for r in v.reasons)


def test_expired_intent_is_denied():
    v = _evaluate(_intent(), now=EXP + timedelta(seconds=1), approval_valid=True)
    assert v.allowed is False and any("expired" in r for r in v.reasons)


def test_action_not_in_allowlist_is_denied():
    v = _evaluate(_intent(), policy=_policy(allowed_actions=frozenset({"ctf_redeem"})), approval_valid=True)
    assert v.allowed is False and any("action" in r for r in v.reasons)


def test_non_allowlisted_contract_is_denied():
    v = _evaluate(_intent(to_address="0xEvil"), approval_valid=True)
    assert v.allowed is False and any("contract" in r for r in v.reasons)


def test_over_notional_cap_is_denied():
    v = _evaluate(_intent(notional_usd=Decimal("100.01")),
                  policy=_policy(approval_threshold_usd=Decimal("1000")), approval_valid=True)
    assert v.allowed is False and any("notional" in r for r in v.reasons)


def test_over_slippage_cap_is_denied():
    v = _evaluate(_intent(max_slippage=Decimal("0.06")), approval_valid=True)
    assert v.allowed is False and any("slippage" in r for r in v.reasons)


def test_above_threshold_without_approval_is_denied():
    v = _evaluate(_intent(notional_usd=Decimal("80")), approval_valid=False)  # 80 > 50
    assert v.allowed is False and any("approval" in r for r in v.reasons)


def test_above_threshold_with_approval_is_allowed():
    v = _evaluate(_intent(notional_usd=Decimal("80")), approval_valid=True)
    assert v.allowed is True


# --- exact-allowance rules (erc20_approve) ----------------------------------

def _approve(**over) -> Intent:
    base = dict(action="erc20_approve", to_address="0xCTF", approve_spender="0xExchange",
                approve_amount=Decimal("100"), notional_usd=Decimal("0"))
    base.update(over)
    return _intent(**base)


def test_bounded_approve_to_allowlisted_spender_is_allowed():
    assert _evaluate(_approve(), approval_valid=True).allowed is True


def test_approve_to_non_allowlisted_spender_is_denied():
    v = _evaluate(_approve(approve_spender="0xEvil"), approval_valid=True)
    assert v.allowed is False and any("spender" in r for r in v.reasons)


def test_missing_or_nonpositive_allowance_is_denied():
    v1 = _evaluate(_approve(approve_amount=None), approval_valid=True)
    v2 = _evaluate(_approve(approve_amount=Decimal("0")), approval_valid=True)
    assert v1.allowed is False and v2.allowed is False


def test_unreasonably_large_allowance_is_denied_as_de_facto_infinite():
    # an absurd allowance is the "infinite approval" anti-pattern -> deny
    v = _evaluate(_approve(approve_amount=Decimal("1000000000")),
                  policy=_policy(max_notional_usd=Decimal("100")), approval_valid=True)
    assert v.allowed is False and any("allowance" in r for r in v.reasons)


def test_multiple_violations_all_reported():
    v = _evaluate(_intent(chain_id=1, to_address="0xEvil", max_slippage=Decimal("0.9"),
                          notional_usd=Decimal("999")),
                  approval_valid=False)
    assert v.allowed is False
    assert len(v.reasons) >= 4
