"""Signals consumer — auto-propose from an injected stream (no Redis). Offline, monkeypatched store.

Asserts: actionable directional signals propose a pending intent; arb/longshot are skipped;
duplicates (same signal id) are skipped; malformed JSON is reported invalid; the consumer never
signs (the audit trail stops at pending_approval).
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import Settings
from app.db import store
from app.orchestrator.consumer import ProcessOutcome, process_message, run_consumer
from app.signer.crypto import LocalSigner

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXCHANGE = "0xExchange"


def _settings(**over) -> Settings:
    base = dict(
        signer_private_key=TEST_PK, approval_secret="s", chain_id=137,
        allowlist_contracts=EXCHANGE, per_trade_cap_usd=Decimal("100"),
        max_slippage=Decimal("0.05"), approval_threshold_usd=Decimal("50"),
        hot_wallet_cap_usd=Decimal("500"), rate_limit_count=10,
        rate_limit_notional_usd=Decimal("1000"), dry_run=True, require_approval_for_all=True,
    )
    base.update(over)
    return Settings(**base)


def _directional_json(signal_id: str = "extreme_correction:m1:1") -> str:
    return (
        '{"id":"' + signal_id + '","time":"2026-06-17T12:00:00+00:00","market_id":"m1",'
        '"condition_id":"c1","strategy":"extreme_correction","kind":"buy_no",'
        '"market_price":"0.18","p":"0.70","edge":"0.5","net_edge":"0.06",'
        '"recommended_size_usd":"40","recommended_size_pct":"0.04","confidence":"0.5",'
        '"gate_passed":true,"gate":{"m":"0.18","half_spread":"0.02","slippage":"0.01",'
        '"gas":"0.01","margin":"0.05","p_lo":"0.65","threshold":"0.22"}}'
    )


def _arb_json() -> str:
    return (
        '{"id":"set_arb:m2:1","time":"2026-06-17T12:00:00+00:00","market_id":"m2",'
        '"condition_id":"c2","strategy":"set_arb","kind":"long_set","market_price":"0.95",'
        '"p":null,"edge":"0.05","net_edge":"0.03","recommended_size_usd":"0",'
        '"recommended_size_pct":"0","confidence":"1","gate_passed":true,"gate":null}'
    )


@pytest.fixture
def fake_store(monkeypatch):
    cap: dict = {"intents": [], "audit": []}

    async def allocate_nonce(session, address, chain_id):
        return len(cap["intents"]) + 1

    async def insert_intent(session, intent, intent_hash):
        cap["intents"].append(intent)

    async def append_audit(session, *, intent_id, event, detail=None, actor=None, tx_hash=None, time=None):
        cap["audit"].append(event)

    monkeypatch.setattr(store, "allocate_nonce", allocate_nonce)
    monkeypatch.setattr(store, "insert_intent", insert_intent)
    monkeypatch.setattr(store, "append_audit", append_audit)
    return cap


async def test_actionable_directional_is_proposed(fake_store):
    outcome = await process_message(
        object(), _directional_json(), settings=_settings(), signer=LocalSigner(TEST_PK),
        now=NOW, intent_id="i-1", seen=set(),
    )
    assert outcome.status == "proposed"
    assert outcome.intent_id == "i-1"
    # Consumer never signs: the trail stops at pending_approval.
    assert fake_store["audit"] == ["formed", "pending_approval"]


async def test_arb_is_skipped_unsupported(fake_store):
    outcome = await process_message(
        object(), _arb_json(), settings=_settings(), signer=LocalSigner(TEST_PK),
        now=NOW, intent_id="i-2", seen=set(),
    )
    assert outcome.status == "skipped_unsupported"
    assert fake_store["intents"] == []  # nothing formed


async def test_duplicate_signal_is_skipped(fake_store):
    seen: set[str] = set()
    first = await process_message(
        object(), _directional_json(), settings=_settings(), signer=LocalSigner(TEST_PK),
        now=NOW, intent_id="i-1", seen=seen,
    )
    second = await process_message(
        object(), _directional_json(), settings=_settings(), signer=LocalSigner(TEST_PK),
        now=NOW, intent_id="i-2", seen=seen,
    )
    assert first.status == "proposed"
    assert second.status == "skipped_duplicate"
    assert len(fake_store["intents"]) == 1  # only proposed once


async def test_invalid_json_is_reported(fake_store):
    outcome = await process_message(
        object(), "{not json}", settings=_settings(), signer=LocalSigner(TEST_PK),
        now=NOW, intent_id="i-3", seen=set(),
    )
    assert outcome.status == "invalid"
    assert fake_store["intents"] == []


async def test_run_consumer_over_a_stream(fake_store):
    msgs = [_directional_json("extreme_correction:m1:1"), _arb_json(), _directional_json("extreme_correction:m1:1")]

    async def stream() -> AsyncIterable[str]:
        for m in msgs:
            yield m

    class _SM:
        def __call__(self):
            return self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def commit(self):
            return None

    outcomes: list[ProcessOutcome] = []
    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"i-{counter['n']}"

    await run_consumer(
        stream(), settings=_settings(), signer=LocalSigner(TEST_PK), sessionmaker=_SM(),
        now_fn=lambda: NOW, id_fn=next_id, on_outcome=outcomes.append,
    )
    assert [o.status for o in outcomes] == ["proposed", "skipped_unsupported", "skipped_duplicate"]
