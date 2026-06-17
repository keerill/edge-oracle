"""Signer crypto layer (Phase 4, offline local key) — EIP-712 sign + address recovery.

The signer signs a real EIP-712 typed message binding the intent's hash + on-chain bounds
(chainId/expiry/nonce). Because ``intent_hash`` is SHA-256 over the full canonical intent, the
signature commits to every field. Key invariants: the signer signs ONLY after the policy allows,
the recovered address equals the signer's, the private key never appears in the output, and a
tampered intent never yields a usable signature. (Mapping to Polymarket's exact order/tx schema
is the later integration; the production key swaps to AWS KMS behind the same interface.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.intent import Intent, IntentEnvelope
from app.signer.crypto import LocalSigner, recover_signer
from app.signer.eip712 import intent_typed_data
from app.signer.policy import SignerPolicy
from app.signer.service import sign_intent

# Well-known throwaway test key (Anvil/Hardhat account #0) — public, testnet-only, never a secret.
TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = NOW + timedelta(minutes=5)


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1", created_at=NOW, expiry=EXP, source_signal_id="s1",
        action="clob_order", chain_id=137, market_id="m1", condition_id="c1",
        side="buy_no", size=Decimal("400"), max_price=Decimal("0.30"),
        max_slippage=Decimal("0.01"), notional_usd=Decimal("40"),
        to_address="0xExchange", token_id=None, approve_spender=None,
        approve_amount=None, nonce=7,
    )
    base.update(over)
    return Intent(**base)


def _policy(**over) -> SignerPolicy:
    base = dict(
        chain_id=137,
        allowed_actions=frozenset({"clob_order", "ctf_split", "erc20_approve"}),
        allowlisted_contracts=frozenset({"0xExchange"}),
        allowlisted_spenders=frozenset({"0xExchange"}),
        max_notional_usd=Decimal("100"),
        max_slippage=Decimal("0.05"),
        approval_threshold_usd=Decimal("50"),
    )
    base.update(over)
    return SignerPolicy(**base)


# --- eip712 typed data ------------------------------------------------------

def test_typed_data_binds_the_intent_hash_and_bounds():
    env = IntentEnvelope.seal(_intent())
    td = intent_typed_data(env)
    assert td["message_data"]["intentHash"] == bytes.fromhex(env.intent_hash)
    assert td["message_data"]["chainId"] == 137
    assert td["message_data"]["expiry"] == int(EXP.timestamp())
    assert td["message_data"]["nonce"] == 7
    assert td["domain_data"]["chainId"] == 137


# --- local signer -----------------------------------------------------------

def test_signer_address_matches_known_key():
    assert LocalSigner(TEST_PK).address == TEST_ADDR


def test_sign_then_recover_round_trips():
    signer = LocalSigner(TEST_PK)
    env = IntentEnvelope.seal(_intent())
    signed = signer.sign(env)
    assert signed.signer_address == TEST_ADDR
    assert signed.v in (27, 28)
    assert len(bytes.fromhex(signed.signature.removeprefix("0x"))) == 65
    # independent recovery (what the relay/audit would do) returns the signer
    assert recover_signer(env, signed.signature) == TEST_ADDR


def test_signature_does_not_leak_the_private_key():
    signer = LocalSigner(TEST_PK)
    signed = signer.sign(IntentEnvelope.seal(_intent()))
    blob = signed.model_dump_json()
    assert TEST_PK not in blob and TEST_PK.removeprefix("0x") not in blob


def test_a_different_intent_recovers_but_to_a_different_hash():
    signer = LocalSigner(TEST_PK)
    a = signer.sign(IntentEnvelope.seal(_intent(notional_usd=Decimal("40"))))
    b = signer.sign(IntentEnvelope.seal(_intent(notional_usd=Decimal("41"))))
    assert a.signature != b.signature  # different intent -> different signature
    assert a.intent_hash != b.intent_hash


# --- the gated service ------------------------------------------------------

def test_service_signs_only_when_policy_allows():
    signer = LocalSigner(TEST_PK)
    env = IntentEnvelope.seal(_intent(notional_usd=Decimal("40")))  # below threshold
    result = sign_intent(env, _policy(), signer, now=NOW, approval_valid=False)
    assert result.signed is not None
    assert result.rejected_reasons == ()
    assert recover_signer(env, result.signed.signature) == TEST_ADDR


def test_service_refuses_to_sign_when_policy_denies():
    signer = LocalSigner(TEST_PK)
    env = IntentEnvelope.seal(_intent(to_address="0xEvil"))  # not allowlisted
    result = sign_intent(env, _policy(), signer, now=NOW, approval_valid=True)
    assert result.signed is None
    assert any("contract" in r for r in result.rejected_reasons)


def test_service_refuses_tampered_envelope():
    signer = LocalSigner(TEST_PK)
    good = IntentEnvelope.seal(_intent())
    doctored = IntentEnvelope(intent=_intent(to_address="0xEvil"), intent_hash=good.intent_hash)
    result = sign_intent(doctored, _policy(), signer, now=NOW, approval_valid=True)
    assert result.signed is None
    assert any("hash" in r for r in result.rejected_reasons)


def test_service_refuses_above_threshold_without_approval():
    signer = LocalSigner(TEST_PK)
    env = IntentEnvelope.seal(_intent(notional_usd=Decimal("80")))  # > 50 threshold
    assert sign_intent(env, _policy(), signer, now=NOW, approval_valid=False).signed is None
    assert sign_intent(env, _policy(), signer, now=NOW, approval_valid=True).signed is not None
