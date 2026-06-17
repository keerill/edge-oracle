"""Order submission — the last mile. DRY-RUN is the only implemented path.

The advisor → intent → breakers → approval → signer chain produces a ``SignedIntent``. This
module is what would hand it to Polymarket. In **dry-run** (the default, see ``EDGE_EXEC_DRY_RUN``)
it does NO network I/O: it builds the payload summary that *would* be submitted and returns it for
the audit trail — so the whole pipeline is exercisable end-to-end with zero risk.

Live submission is deliberately NOT implemented here: Polymarket executes via its CLOB API with a
provider-specific signed-order schema + L2 auth headers (real credentials), which are external
dependencies. Calling ``submit`` with ``dry_run=False`` raises ``NotImplementedError`` rather than
guess a wire format — a wrong guess with real money is the failure mode we refuse.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.intent import Intent
from app.signer.crypto import SignedIntent

SubmitStatus = Literal["dry_run", "submitted", "failed"]


class SubmitResult(BaseModel):
    """The outcome of a submit attempt. In dry-run, ``status='dry_run'`` and ``order_ref`` is the
    signed intent's hash (the stable handle the audit row keys off); no network was touched."""

    model_config = ConfigDict(frozen=True)

    status: SubmitStatus
    order_ref: str | None  # the order/tx handle (dry-run: the intent hash)
    payload: dict  # the order summary that was (or would be) sent — float-free


def _order_payload(intent: Intent, signed: SignedIntent) -> dict:
    """The float-free summary of the order that would be posted (Decimals as strings)."""
    return {
        "intent_id": intent.intent_id,
        "action": intent.action,
        "side": intent.side,
        "market_id": intent.market_id,
        "condition_id": intent.condition_id,
        "size": str(intent.size),
        "max_price": None if intent.max_price is None else str(intent.max_price),
        "notional_usd": str(intent.notional_usd),
        "to_address": intent.to_address,
        "nonce": intent.nonce,
        "signer_address": signed.signer_address,
        "intent_hash": signed.intent_hash,
        "signature": signed.signature,
    }


def submit(intent: Intent, signed: SignedIntent, *, dry_run: bool) -> SubmitResult:
    """Submit a signed intent. ``dry_run=True`` records the payload without any network I/O;
    ``dry_run=False`` raises — live Polymarket CLOB submission is not wired (needs their order
    schema + API credentials)."""
    payload = _order_payload(intent, signed)
    if dry_run:
        return SubmitResult(status="dry_run", order_ref=signed.intent_hash, payload=payload)
    raise NotImplementedError(
        "live order submission is not implemented: Polymarket CLOB requires a provider-specific "
        "signed-order schema + API credentials (external). Run with EDGE_EXEC_DRY_RUN=true."
    )


def notional_within(amount: Decimal, cap: Decimal) -> bool:
    """Tiny guard reused by callers: amount must be a positive, capped notional."""
    return Decimal(0) < amount <= cap
