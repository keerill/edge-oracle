"""Build the signer's policy + key from settings (the composition root for the signer service)."""

from __future__ import annotations

from app.config import Settings
from app.signer.crypto import LocalSigner
from app.signer.policy import SignerPolicy

_ALLOWED_ACTIONS = frozenset(
    {"clob_order", "ctf_split", "ctf_merge", "ctf_redeem", "erc20_approve"}
)


def policy_from_settings(settings: Settings) -> SignerPolicy:
    """The signer's independent policy, projected from the EDGE_EXEC_* allowlists + caps."""
    return SignerPolicy(
        chain_id=settings.chain_id,
        allowed_actions=_ALLOWED_ACTIONS,
        allowlisted_contracts=settings.allowlisted_contracts,
        allowlisted_spenders=settings.allowlisted_spenders,
        max_notional_usd=settings.per_trade_cap_usd,
        max_slippage=settings.max_slippage,
        approval_threshold_usd=settings.approval_threshold_usd,
    )


def signer_from_settings(settings: Settings) -> LocalSigner | None:
    """The local-key signer, or ``None`` when no key is configured (the service then refuses)."""
    if not settings.signer_private_key:
        return None
    return LocalSigner(settings.signer_private_key)
