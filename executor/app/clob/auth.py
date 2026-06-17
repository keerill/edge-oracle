"""Polymarket L2 (API-key) authentication headers — pure HMAC, no I/O.

Each authenticated request carries an HMAC-SHA256 signature over ``timestamp + method +
requestPath + body`` keyed by the (base64url-decoded) API secret, encoded base64url. Mirrors
py-clob-client's ``build_hmac_signature``. The header *names* are version-sensitive (py-clob-client
has used both ``POLY_*`` and ``POLY-*`` forms); the default here is the underscore form — verify
against the target API version before enabling real submission.

NOTHING here logs or persists the secret/key/passphrase; they are passed in and used transiently.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# Header names (underscore form). If the target API expects the dash form, override at the call
# site — this is one of the documented "verify before live" knobs.
H_ADDRESS = "POLY_ADDRESS"
H_SIGNATURE = "POLY_SIGNATURE"
H_TIMESTAMP = "POLY_TIMESTAMP"
H_API_KEY = "POLY_API_KEY"
H_PASSPHRASE = "POLY_PASSPHRASE"


def build_hmac_signature(secret: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    """base64url(HMAC_SHA256(base64url_decode(secret), timestamp+method+request_path+body)).

    ``secret`` is the base64url API secret string; the message concatenates the parts with no
    separators (py-clob-client semantics)."""
    key = base64.urlsafe_b64decode(secret)
    message = f"{timestamp}{method}{request_path}{body}".encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()


def l2_headers(
    *,
    address: str,
    api_key: str,
    secret: str,
    passphrase: str,
    timestamp: str,
    method: str,
    request_path: str,
    body: str = "",
) -> dict[str, str]:
    """The full set of L2 headers for an authenticated CLOB request."""
    return {
        H_ADDRESS: address,
        H_API_KEY: api_key,
        H_PASSPHRASE: passphrase,
        H_TIMESTAMP: timestamp,
        H_SIGNATURE: build_hmac_signature(secret, timestamp, method, request_path, body),
    }
