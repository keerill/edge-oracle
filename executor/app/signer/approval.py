"""Approval tokens — an HMAC bound to the exact intent hash + an expiry (pure, no state).

Above the approval threshold, a human approval mints a token over ``intent_hash | expiry``; the
signer recomputes the HMAC and checks the TTL. Binding to the hash makes "approve cheap, swap in
expensive" impossible; the expiry bounds replay. Single-use is enforced separately (the
``exec_approvals.consumed`` flag), since that needs durable state this pure module avoids.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime


def _mac(intent_hash: str, exp_epoch: int, secret: str) -> str:
    return hmac.new(
        secret.encode(), f"{intent_hash}|{exp_epoch}".encode(), hashlib.sha256
    ).hexdigest()


def mint_approval_token(intent_hash: str, expires_at: datetime, secret: str) -> str:
    """Mint ``"<exp_epoch>.<hmac>"`` binding the approval to this intent hash until ``expires_at``."""
    exp = int(expires_at.timestamp())
    return f"{exp}.{_mac(intent_hash, exp, secret)}"


def verify_approval_token(
    token: str | None, intent_hash: str, secret: str, *, now: datetime
) -> bool:
    """True iff ``token`` is a well-formed, unexpired HMAC over exactly ``intent_hash``."""
    if not token or "." not in token:
        return False
    exp_str, mac = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if now.timestamp() > exp:
        return False
    return hmac.compare_digest(mac, _mac(intent_hash, exp, secret))
