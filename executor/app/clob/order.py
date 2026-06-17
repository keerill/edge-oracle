"""Build a Polymarket CLOB order (EIP-712) from our ``Intent`` — pure, no I/O, no key.

Maps a directional ``clob_order`` Intent (buy the YES or NO outcome token) to the exchange's
``Order`` struct + its EIP-712 typed data, and to the JSON body posted to ``/order``. Amounts come
from :mod:`app.clob.amounts` (6-dp base units). ``side`` is ``BUY`` for our buy_* intents (the
``tokenId`` is what selects YES vs NO); the on-chain enum is 0=BUY / 1=SELL.

Provider-specific details (struct field set, domain name/version, the posted JSON shape, GTC/FOK
order types, NegRisk's separate exchange) are taken from the public py-clob-client schema and are
**configurable** — verify them against the live API before enabling real submission. ``salt`` and
``nonce`` are injected (pure/testable) the way the rest of the module injects clocks/ids.
"""

from __future__ import annotations

from typing import Any

from app.clob.amounts import order_amounts
from app.models.intent import Intent

# EIP-712 type for the CTF Exchange Order struct.
_ORDER_TYPES: dict[str, list[dict[str, str]]] = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_SIDE_ENUM = {"BUY": 0, "SELL": 1}


def order_side(intent: Intent) -> str:
    """``BUY``/``SELL`` for the order. Our directional intents are ``buy_yes``/``buy_no`` — both
    BUY the respective outcome token (``tokenId`` distinguishes YES vs NO)."""
    if intent.side in ("buy_yes", "buy_no"):
        return "BUY"
    if intent.side in ("sell_yes", "sell_no"):
        return "SELL"
    raise ValueError(f"intent side {intent.side!r} is not a CLOB order side")


def build_order(
    intent: Intent,
    *,
    token_id: int,
    maker: str,
    signer_address: str,
    salt: int,
    fee_rate_bps: int,
    signature_type: int,
    taker: str = _ZERO_ADDRESS,
) -> dict[str, Any]:
    """The exchange ``Order`` struct (integer amounts, enum side) ready for EIP-712 encoding.

    ``price`` is the intent's ``max_price`` (the limit you'll pay); ``size`` is ``intent.size``.
    ``token_id`` is the ERC-1155 outcome-token id (must be resolved upstream — our Intent carries
    it as a string once the market mapping lands)."""
    if intent.action != "clob_order":
        raise ValueError(f"not a clob_order intent: action={intent.action!r}")
    if intent.max_price is None:
        raise ValueError("clob_order intent has no max_price (limit price)")
    side = order_side(intent)
    maker_amount, taker_amount = order_amounts(side, intent.max_price, intent.size)
    return {
        "salt": salt,
        "maker": maker,
        "signer": signer_address,
        "taker": taker,
        "tokenId": token_id,
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": int(intent.expiry.timestamp()),
        "nonce": intent.nonce,
        "feeRateBps": fee_rate_bps,
        "side": _SIDE_ENUM[side],
        "signatureType": signature_type,
    }


def order_typed_data(
    order: dict[str, Any],
    *,
    chain_id: int,
    exchange_address: str,
    domain_name: str,
    domain_version: str,
) -> dict[str, Any]:
    """``encode_typed_data`` kwargs for the order (domain bound to the verifying exchange)."""
    return {
        "domain_data": {
            "name": domain_name,
            "version": domain_version,
            "chainId": chain_id,
            "verifyingContract": exchange_address,
        },
        "message_types": _ORDER_TYPES,
        "message_data": order,
    }


def signed_order_payload(order: dict[str, Any], signature: str, *, owner: str, order_type: str) -> dict[str, Any]:
    """The JSON body POSTed to ``/order``: the order (numeric fields as strings, side as a label),
    the EIP-712 ``signature``, the ``owner`` (L2 API key) and the ``orderType`` (e.g. GTC/FOK).
    The exact field names/shape are provider-version-sensitive — verify against the live API."""
    side_label = "BUY" if order["side"] == _SIDE_ENUM["BUY"] else "SELL"
    return {
        "order": {
            "salt": str(order["salt"]),
            "maker": order["maker"],
            "signer": order["signer"],
            "taker": order["taker"],
            "tokenId": str(order["tokenId"]),
            "makerAmount": str(order["makerAmount"]),
            "takerAmount": str(order["takerAmount"]),
            "expiration": str(order["expiration"]),
            "nonce": str(order["nonce"]),
            "feeRateBps": str(order["feeRateBps"]),
            "side": side_label,
            "signatureType": order["signatureType"],
            "signature": signature,
        },
        "owner": owner,
        "orderType": order_type,
    }
