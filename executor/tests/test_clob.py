"""Polymarket CLOB client — amounts, order EIP-712, L2 auth HMAC, and the mock-HTTP poster.

All offline: amount/auth/order are pure; the client runs through httpx.MockTransport (no network,
no creds). These pin the LOGIC (money-critical amount scaling, recoverable order signature, HMAC
construction); provider field-level details are flagged in the modules as verify-before-live.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.clob.amounts import order_amounts
from app.clob.auth import build_hmac_signature, l2_headers
from app.clob.client import ClobError, place_order
from app.clob.order import build_order, order_side, order_typed_data, signed_order_payload
from app.models.intent import Intent
from app.signer.crypto import LocalSigner

TEST_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2026, 6, 17, 12, 5, 0, tzinfo=UTC)
EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"


def _intent(**over) -> Intent:
    base = dict(
        intent_id="i-1", created_at=NOW, expiry=EXP, source_signal_id="s1",
        action="clob_order", chain_id=137, market_id="m1", condition_id="c1",
        side="buy_no", size=Decimal("400"), max_price=Decimal("0.30"),
        max_slippage=Decimal("0.01"), notional_usd=Decimal("120"),
        to_address=EXCHANGE, token_id="123", approve_spender=None,
        approve_amount=None, nonce=7,
    )
    base.update(over)
    return Intent(**base)


# --- amounts -----------------------------------------------------------------


def test_buy_amounts_scale_to_6dp():
    # BUY 400 shares @ 0.30: pay 120 USDC, receive 400 tokens. 6-dp base units.
    maker, taker = order_amounts("BUY", Decimal("0.30"), Decimal("400"))
    assert maker == 120_000_000  # 120 USDC
    assert taker == 400_000_000  # 400 tokens


def test_sell_amounts_are_inverted():
    maker, taker = order_amounts("SELL", Decimal("0.30"), Decimal("400"))
    assert maker == 400_000_000  # give 400 tokens
    assert taker == 120_000_000  # receive 120 USDC


def test_amounts_round_half_up_at_6dp():
    # 0.333333 * 1 = 0.333333 (already 6dp); 0.3333335 -> 0.333334 (half-up).
    maker, _ = order_amounts("BUY", Decimal("0.3333335"), Decimal("1"))
    assert maker == 333_334


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("1.01"), Decimal("-0.1")])
def test_amounts_reject_bad_price(price):
    with pytest.raises(ValueError):
        order_amounts("BUY", price, Decimal("10"))


def test_amounts_reject_bad_side_and_size():
    with pytest.raises(ValueError):
        order_amounts("HOLD", Decimal("0.3"), Decimal("10"))
    with pytest.raises(ValueError):
        order_amounts("BUY", Decimal("0.3"), Decimal("0"))


# --- order build + EIP-712 signing -------------------------------------------


def test_order_side_maps_buys_to_BUY():
    assert order_side(_intent(side="buy_yes")) == "BUY"
    assert order_side(_intent(side="buy_no")) == "BUY"


def test_build_order_fields_and_amounts():
    order = build_order(
        _intent(), token_id=123, maker=TEST_ADDR, signer_address=TEST_ADDR,
        salt=42, fee_rate_bps=0, signature_type=0,
    )
    assert order["side"] == 0  # BUY enum
    assert order["tokenId"] == 123
    assert order["makerAmount"] == 120_000_000  # 0.30 * 400 USDC
    assert order["takerAmount"] == 400_000_000
    assert order["expiration"] == int(EXP.timestamp())
    assert order["nonce"] == 7


def test_order_signature_is_recoverable():
    order = build_order(
        _intent(), token_id=123, maker=TEST_ADDR, signer_address=TEST_ADDR,
        salt=42, fee_rate_bps=0, signature_type=0,
    )
    typed = order_typed_data(
        order, chain_id=137, exchange_address=EXCHANGE,
        domain_name="Polymarket CTF Exchange", domain_version="1",
    )
    signer = LocalSigner(TEST_PK)
    signature = signer.sign_eip712(typed)
    # Recover the signer from the same typed data -> must be the test address.
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    recovered = Account.recover_message(encode_typed_data(**typed), signature=signature)
    assert recovered == TEST_ADDR


def test_signed_order_payload_shape():
    order = build_order(
        _intent(), token_id=123, maker=TEST_ADDR, signer_address=TEST_ADDR,
        salt=42, fee_rate_bps=0, signature_type=0,
    )
    payload = signed_order_payload(order, "0xsig", owner="api-key-1", order_type="GTC")
    assert payload["owner"] == "api-key-1"
    assert payload["orderType"] == "GTC"
    assert payload["order"]["side"] == "BUY"
    assert payload["order"]["makerAmount"] == "120000000"  # stringified
    assert payload["order"]["signature"] == "0xsig"


# --- L2 auth HMAC ------------------------------------------------------------


def test_hmac_signature_matches_reference():
    secret = base64.urlsafe_b64encode(b"super-secret-key").decode()
    sig = build_hmac_signature(secret, "1700000000", "POST", "/order", '{"a":1}')
    # Recompute the reference independently.
    key = base64.urlsafe_b64decode(secret)
    expected = base64.urlsafe_b64encode(
        hmac.new(key, b"1700000000POST/order" + b'{"a":1}', hashlib.sha256).digest()
    ).decode()
    assert sig == expected


def test_l2_headers_include_all_fields():
    secret = base64.urlsafe_b64encode(b"k").decode()
    h = l2_headers(
        address=TEST_ADDR, api_key="key", secret=secret, passphrase="pass",
        timestamp="1700000000", method="POST", request_path="/order", body="{}",
    )
    assert h["POLY_ADDRESS"] == TEST_ADDR
    assert h["POLY_API_KEY"] == "key"
    assert h["POLY_PASSPHRASE"] == "pass"
    assert h["POLY_TIMESTAMP"] == "1700000000"
    assert h["POLY_SIGNATURE"]  # present


# --- client (mock HTTP) ------------------------------------------------------


async def test_place_order_posts_and_parses():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"orderId": "abc", "status": "live"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await place_order(
            client, "https://clob.test", {"order": {}}, {"POLY_API_KEY": "key"}
        )
    assert result == {"orderId": "abc", "status": "live"}
    assert captured["url"] == "https://clob.test/order"
    assert captured["headers"]["poly_api_key"] == "key"


async def test_place_order_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid order"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ClobError) as exc:
            await place_order(client, "https://clob.test", {}, {})
    assert exc.value.status == 400
    assert exc.value.body == {"error": "invalid order"}
