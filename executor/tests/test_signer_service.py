"""Signer HTTP service (POST /sign) — policy-gated signing over the wire (TestClient, no network).

The signer re-runs its own policy; the endpoint signs only when allowed and returns 403 + reasons
otherwise. Settings are overridden per-test (key, allowlists, caps) via get_settings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.models.intent import Intent, IntentEnvelope
from app.signer.approval import mint_approval_token
from app.signer.crypto import recover_signer
from app.signer.main import app

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2100, 1, 1, tzinfo=UTC)  # far future so the intent never expires in the test
SECRET = "approval-secret"


def _settings(**over) -> Settings:
    base = dict(
        signer_private_key=TEST_PK,
        approval_secret=SECRET,
        chain_id=137,
        allowlist_contracts="0xExchange",
        allowlist_spenders="0xExchange",
        per_trade_cap_usd=Decimal("100"),
        max_slippage=Decimal("0.05"),
        approval_threshold_usd=Decimal("50"),
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def client(request):
    over = getattr(request, "param", {})
    app.dependency_overrides = {}
    get_settings.cache_clear()
    # Override the module-level settings singleton used by the endpoint.
    import app.signer.main as main

    main.get_settings = lambda: _settings(**over)  # type: ignore[assignment]
    try:
        yield TestClient(app)
    finally:

        from app import config

        main.get_settings = config.get_settings


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


def _body(intent, token=None):
    env = IntentEnvelope.seal(intent)
    payload = {"envelope": env.model_dump(mode="json")}
    if token is not None:
        payload["approval_token"] = token
    return env, payload


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_sign_below_threshold_returns_recoverable_signature(client):
    env, body = _body(_intent(notional_usd=Decimal("40")))
    res = client.post("/sign", json=body)
    assert res.status_code == 200
    signed = res.json()
    assert signed["signer_address"] == TEST_ADDR
    assert recover_signer(env, signed["signature"]) == TEST_ADDR


def test_non_allowlisted_contract_is_403(client):
    _, body = _body(_intent(to_address="0xEvil"))
    res = client.post("/sign", json=body)
    assert res.status_code == 403
    assert any("contract" in r for r in res.json()["detail"]["rejected"])


def test_above_threshold_needs_a_bound_token(client):
    env, body_no_tok = _body(_intent(notional_usd=Decimal("80")))
    assert client.post("/sign", json=body_no_tok).status_code == 403
    token = mint_approval_token(env.intent_hash, EXP, SECRET)
    _, body_tok = _body(_intent(notional_usd=Decimal("80")), token=token)
    assert client.post("/sign", json=body_tok).status_code == 200


@pytest.mark.parametrize("client", [{"signer_private_key": None}], indirect=True)
def test_503_when_no_key_configured(client):
    _, body = _body(_intent())
    assert client.post("/sign", json=body).status_code == 503
