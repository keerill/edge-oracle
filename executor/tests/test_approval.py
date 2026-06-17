"""Approval token — HMAC bound to the EXACT intent hash + a TTL (pure crypto, no state).

A human approval above the threshold mints a token bound to that intent's hash; the signer
verifies it. Binding to the hash stops "approve a cheap intent, swap in an expensive one"; the TTL
bounds replay. (Single-use is enforced separately via the exec_approvals.consumed flag.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.signer.approval import mint_approval_token, verify_approval_token

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = NOW + timedelta(minutes=5)
SECRET = "signer-approval-secret"
HASH = "aa" * 32


def test_minted_token_verifies_within_ttl():
    token = mint_approval_token(HASH, EXP, SECRET)
    assert verify_approval_token(token, HASH, SECRET, now=NOW) is True


def test_token_is_bound_to_the_intent_hash():
    token = mint_approval_token(HASH, EXP, SECRET)
    assert verify_approval_token(token, "bb" * 32, SECRET, now=NOW) is False


def test_expired_token_is_rejected():
    token = mint_approval_token(HASH, EXP, SECRET)
    assert verify_approval_token(token, HASH, SECRET, now=EXP + timedelta(seconds=1)) is False


def test_wrong_secret_is_rejected():
    token = mint_approval_token(HASH, EXP, SECRET)
    assert verify_approval_token(token, HASH, "other-secret", now=NOW) is False


def test_tampered_token_is_rejected():
    token = mint_approval_token(HASH, EXP, SECRET)
    exp_part, mac = token.split(".", 1)
    tampered = f"{exp_part}.{'0' * len(mac)}"
    assert verify_approval_token(tampered, HASH, SECRET, now=NOW) is False


def test_malformed_token_is_rejected():
    for bad in ("", "no-dot", "notanumber.abc", None):
        assert verify_approval_token(bad, HASH, SECRET, now=NOW) is False
