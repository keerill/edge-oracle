"""Execution pipeline — form → breakers → approval → sign → dry-run submit, with audit.

Offline: the DB store calls are monkeypatched to capture the audit trail, so these assert the
control flow + the two safety properties (dry-run never broadcasts; semi-auto blocks unsigned
trades) without a database. A real signer key signs so the happy path proves a recoverable sig.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.breakers.checks import BreakerLimits, BreakerState
from app.db import store
from app.models.advised import AdvisedSignalView, GateBreakdownView
from app.models.intent import compute_intent_hash
from app.orchestrator import pipeline
from app.orchestrator.intents import intent_from_signal
from app.signer.approval import mint_approval_token
from app.signer.crypto import LocalSigner
from app.signer.policy import SignerPolicy

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2100, 1, 1, tzinfo=UTC)
SECRET = "approval-secret"
EXCHANGE = "0xExchange"
NONCE = 5


def _advised(**over) -> AdvisedSignalView:
    base = dict(
        id="extreme_correction:m1:1", time=NOW, market_id="m1", condition_id="c1",
        strategy="extreme_correction", kind="buy_no", market_price=Decimal("0.18"),
        p=Decimal("0.70"), edge=Decimal("0.5"), net_edge=Decimal("0.06"),
        recommended_size_usd=Decimal("40"), recommended_size_pct=Decimal("0.04"),
        confidence=Decimal("0.5"), gate_passed=True,
        gate=GateBreakdownView(
            m=Decimal("0.18"), half_spread=Decimal("0.02"), slippage=Decimal("0.01"),
            gas=Decimal("0.01"), margin=Decimal("0.05"), p_lo=Decimal("0.65"),
            threshold=Decimal("0.22"),
        ),
    )
    base.update(over)
    return AdvisedSignalView(**base)


def _policy() -> SignerPolicy:
    return SignerPolicy(
        chain_id=137,
        allowed_actions=frozenset({"clob_order"}),
        allowlisted_contracts=frozenset({EXCHANGE}),
        allowlisted_spenders=frozenset(),
        max_notional_usd=Decimal("100"),
        max_slippage=Decimal("0.05"),
        approval_threshold_usd=Decimal("50"),
    )


def _limits(*, enabled: bool) -> BreakerLimits:
    return BreakerLimits(
        enabled=enabled, per_trade_cap_usd=Decimal("100"), max_slippage=Decimal("0.05"),
        rate_limit_count=10, rate_limit_notional_usd=Decimal("1000"),
        hot_wallet_cap_usd=Decimal("500"),
    )


def _state() -> BreakerState:
    return BreakerState(
        hot_balance_usd=Decimal("10000"), window_trade_count=0,
        window_notional_usd=Decimal("0"), allowlisted_contracts=frozenset({EXCHANGE}),
        allowlisted_spenders=frozenset(),
    )


@pytest.fixture
def audit(monkeypatch):
    """Capture the audit events; stub the DB writes + nonce allocator."""
    events: list[str] = []

    async def fake_allocate_nonce(session, address, chain_id):
        return NONCE

    async def fake_insert_intent(session, intent, intent_hash):
        return None

    async def fake_append_audit(session, *, intent_id, event, detail=None, actor=None, tx_hash=None, time=None):
        events.append(event)

    monkeypatch.setattr(store, "allocate_nonce", fake_allocate_nonce)
    monkeypatch.setattr(store, "insert_intent", fake_insert_intent)
    monkeypatch.setattr(store, "append_audit", fake_append_audit)
    return events


def _valid_token(advised: AdvisedSignalView, *, notional: Decimal) -> str:
    """Mint a token bound to the exact intent the pipeline will form (same nonce/now/ids)."""
    # Must mirror the pipeline exactly: it forms the intent with limits.max_slippage (0.05).
    intent = intent_from_signal(
        advised, notional_usd=notional, nonce=NONCE, clob_exchange_address=EXCHANGE,
        max_slippage=Decimal("0.05"), now=NOW, expiry=EXP, intent_id="i-1",
    )
    return mint_approval_token(compute_intent_hash(intent), EXP, SECRET)


async def _run(*, notional=Decimal("40"), enabled=False, dry_run=True, token=None, require_all=True):
    return await pipeline.execute_signal(
        object(), _advised(), signer=LocalSigner(TEST_PK), policy=_policy(),
        limits=_limits(enabled=enabled), state=_state(), notional_usd=notional,
        clob_exchange_address=EXCHANGE, now=NOW, expiry=EXP, intent_id="i-1",
        dry_run=dry_run, require_approval_for_all=require_all,
        approval_token=token, approval_secret=SECRET,
    )


async def test_happy_path_dry_run_signs_and_records(audit):
    token = _valid_token(_advised(), notional=Decimal("40"))
    result = await _run(token=token)
    assert result.status == "submitted"
    assert result.submission is not None and result.submission.status == "dry_run"
    assert audit == ["formed", "approved", "signed", "submitted"]
    # The signature is real + recoverable to the test key.
    assert result.signed is not None
    assert result.signed.signer_address == TEST_ADDR


async def test_semi_auto_blocks_without_token(audit):
    result = await _run(token=None)
    assert result.status == "pending_approval"
    assert result.signed is None  # never signed
    assert audit == ["formed", "pending_approval"]


async def test_breaker_rejects_over_cap(audit):
    # notional 200 > per-trade cap 100 -> rejected before approval/sign.
    result = await _run(notional=Decimal("200"), token="ignored")
    assert result.status == "breaker_rejected"
    assert any("per-trade cap" in r for r in result.reasons)
    assert audit == ["formed", "breaker_rejected"]


async def test_dry_run_runs_with_master_switch_off(audit):
    # enabled=False but dry_run=True: the simulation is exempt from the master switch.
    token = _valid_token(_advised(), notional=Decimal("40"))
    result = await _run(enabled=False, dry_run=True, token=token)
    assert result.status == "submitted"


async def test_live_run_blocked_when_disabled(audit):
    # enabled=False AND dry_run=False: the master switch rejects (nothing executes).
    result = await _run(enabled=False, dry_run=False, token="ignored")
    assert result.status == "breaker_rejected"
    assert any("execution disabled" in r for r in result.reasons)
