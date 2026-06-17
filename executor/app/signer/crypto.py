"""Local-key signer (Phase 4, offline) + signature recovery.

``LocalSigner`` wraps an eth-account key — the OFFLINE/testnet stand-in for the production AWS KMS
signer, which exposes the same ``address`` / ``sign`` interface but never exports the key. The
private key lives only inside this object (loaded from a secret-managed env in real use); the
output ``SignedIntent`` carries only the signature + the public signer address.

``recover_signer`` reconstructs the signer address from the EIP-712 message + signature — the
independent verification the relay / audit log performs (and the round-trip that proves the
(r, s, v) reconstruction is correct).
"""

from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_typed_data
from pydantic import BaseModel, ConfigDict

from app.models.intent import IntentEnvelope
from app.signer.eip712 import intent_typed_data


class SignedIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    intent_hash: str
    signer_address: str
    r: str  # 0x-hex
    s: str  # 0x-hex
    v: int
    signature: str  # 0x-hex, 65 bytes


class LocalSigner:
    """Holds the key privately; signs EIP-712 intent messages. The key is never logged or returned."""

    def __init__(self, private_key: str) -> None:
        self._account = Account.from_key(private_key)  # key stays inside this object

    @property
    def address(self) -> str:
        return self._account.address

    def sign(self, envelope: IntentEnvelope) -> SignedIntent:
        signable = encode_typed_data(**intent_typed_data(envelope))
        signed = self._account.sign_message(signable)
        return SignedIntent(
            intent_id=envelope.intent.intent_id,
            intent_hash=envelope.intent_hash,
            signer_address=self._account.address,
            r=hex(signed.r),
            s=hex(signed.s),
            v=signed.v,
            signature="0x" + signed.signature.hex().removeprefix("0x"),
        )


def recover_signer(envelope: IntentEnvelope, signature: str) -> str:
    """Recover the signer address from the intent's EIP-712 message + signature."""
    signable = encode_typed_data(**intent_typed_data(envelope))
    return Account.recover_message(signable, signature=signature)
