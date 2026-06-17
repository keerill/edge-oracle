"""Form a signed-able ``Intent`` from an advisor opportunity (pure, no I/O, no clock).

Scope this slice: the DIRECTIONAL path (``extreme_correction`` buy_yes/buy_no), which the
advisor payload fully determines — the side token's midpoint ``m`` and ``half_spread`` give the
ask you pay, and ``notional / ask`` gives the share size. Set-arb is deliberately refused here:
``advise()`` collapses the two legs into one ``market_price`` and drops the per-leg VWAPs, so a
correct, MEV-safe two-leg priced order can't be rebuilt from the live stream — that's a later
phase (richer signal + CTF on-chain legs via the relay). Time/nonce/ids are injected so this
stays a pure function the way the advisor's math modules are.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models.advised import AdvisedSignalView
from app.models.intent import Intent

ONE = Decimal(1)


def intent_from_signal(
    advised: AdvisedSignalView,
    *,
    notional_usd: Decimal,
    nonce: int,
    clob_exchange_address: str,
    max_slippage: Decimal,
    now: datetime,
    expiry: datetime,
    intent_id: str,
) -> Intent:
    """Map a directional ``AdvisedSignalView`` to a ``clob_order`` Intent.

    ``ask = gate.m + gate.half_spread`` (the price you actually pay), ``size = notional / ask``,
    ``max_price = min(1, ask + max_slippage)`` (worst fill we'll accept). Raises ``ValueError``
    for non-directional strategies or a directional signal missing its gate (unpriceable)."""
    if advised.strategy != "extreme_correction":
        raise ValueError(
            f"intent_from_signal supports only directional signals this slice; "
            f"got strategy={advised.strategy!r} (set_arb intent-forming is deferred)"
        )
    if advised.kind not in ("buy_yes", "buy_no"):
        raise ValueError(f"unexpected directional kind {advised.kind!r}")
    if advised.gate is None:
        raise ValueError("directional signal has no gate breakdown; cannot price the ask")

    ask = advised.gate.m + advised.gate.half_spread
    size = notional_usd / ask
    max_price = min(ONE, ask + max_slippage)

    return Intent(
        intent_id=intent_id,
        created_at=now,
        expiry=expiry,
        source_signal_id=advised.id,
        action="clob_order",
        chain_id=137,
        market_id=advised.market_id,
        condition_id=advised.condition_id,
        side=advised.kind,  # buy_yes | buy_no
        size=size,
        max_price=max_price,
        max_slippage=max_slippage,
        notional_usd=notional_usd,
        to_address=clob_exchange_address,
        token_id=None,  # resolved from the market when the on-chain phase lands
        approve_spender=None,
        approve_amount=None,
        nonce=nonce,
    )
