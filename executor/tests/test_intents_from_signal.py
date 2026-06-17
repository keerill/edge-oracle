"""``intent_from_signal`` — the pure bridge from an advisor opportunity to a signed-able Intent.

This slice supports the DIRECTIONAL path (``extreme_correction`` buy_yes/buy_no), which is fully
reconstructable from the ``AdvisedSignal`` (the side midpoint + half-spread give the ask). The
set-arb path is intentionally NOT formed here: ``advise()`` collapses the two legs into a single
``market_price`` (set cost) and drops the per-leg VWAPs, so a correct two-leg priced order can't
be rebuilt from the live payload — that belongs to a later phase that consumes the richer signal
and the CTF on-chain legs. Pure: no I/O, no clock (``now``/``expiry``/``nonce``/``intent_id`` are
injected), exact ``Decimal``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.advised import AdvisedSignalView, GateBreakdownView
from app.models.intent import compute_intent_hash
from app.orchestrator.intents import intent_from_signal

T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
EXP = datetime(2026, 6, 17, 12, 5, 0, tzinfo=UTC)


def _directional(**over) -> AdvisedSignalView:
    """A buy_no extreme-correction at side midpoint m=0.20, half_spread=0.05 -> ask=0.25."""
    gate = GateBreakdownView(
        m=Decimal("0.20"),
        half_spread=Decimal("0.05"),
        slippage=Decimal("0.01"),
        gas=Decimal("0.01"),
        margin=Decimal("0.05"),
        p_lo=Decimal("0.70"),
        threshold=Decimal("0.27"),
    )
    base = dict(
        id="extreme_correction:m1:1750161600000",
        time=T0,
        market_id="m1",
        condition_id="c1",
        strategy="extreme_correction",
        kind="buy_no",
        market_price=Decimal("0.20"),
        p=Decimal("0.75"),
        edge=Decimal("0.50"),
        net_edge=Decimal("0.43"),
        recommended_size_usd=Decimal("100"),
        recommended_size_pct=Decimal("0.10"),
        confidence=Decimal("0.6"),
        gate_passed=True,
        gate=gate,
    )
    base.update(over)
    return AdvisedSignalView(**base)


def _form(advised, **over):
    kw = dict(
        notional_usd=Decimal("100"),
        nonce=7,
        clob_exchange_address="0xExchange",
        max_slippage=Decimal("0.05"),
        now=T0,
        expiry=EXP,
        intent_id="i-1",
    )
    kw.update(over)
    return intent_from_signal(advised, **kw)


def test_directional_maps_to_a_clob_order_with_exact_economics():
    intent = _form(_directional())
    assert intent.action == "clob_order"
    assert intent.side == "buy_no"
    assert intent.market_id == "m1"
    assert intent.condition_id == "c1"
    assert intent.source_signal_id == "extreme_correction:m1:1750161600000"
    assert intent.to_address == "0xExchange"
    assert intent.nonce == 7
    assert intent.chain_id == 137
    # ask = m + half_spread = 0.20 + 0.05 = 0.25; size = notional / ask = 100 / 0.25 = 400
    assert intent.size == Decimal("400")
    assert intent.notional_usd == Decimal("100")
    # worst acceptable price = ask + max_slippage = 0.25 + 0.05 = 0.30
    assert intent.max_price == Decimal("0.30")
    assert intent.max_slippage == Decimal("0.05")


def test_formed_intent_is_hashable_and_deterministic():
    a = _form(_directional())
    b = _form(_directional())
    assert compute_intent_hash(a) == compute_intent_hash(b)


def test_max_price_is_capped_at_one():
    # Deep favourite: ask near 1 + slippage would exceed 1; max_price must clamp to 1.
    gate = GateBreakdownView(
        m=Decimal("0.98"), half_spread=Decimal("0.01"), slippage=Decimal("0.01"),
        gas=Decimal("0.01"), margin=Decimal("0.05"), p_lo=Decimal("0.95"), threshold=Decimal("0.99"),
    )
    intent = _form(_directional(kind="buy_yes", gate=gate, market_price=Decimal("0.98")))
    assert intent.max_price == Decimal("1")


def test_buy_yes_side_is_carried_through():
    intent = _form(_directional(kind="buy_yes"))
    assert intent.side == "buy_yes"


def test_arb_is_not_formed_this_slice():
    arb = _directional(strategy="set_arb", kind="long_set", gate=None, p=None)
    with pytest.raises(ValueError, match="set_arb"):
        _form(arb)


def test_directional_without_a_gate_is_rejected():
    # No gate => no priced ask => cannot form a safe order.
    with pytest.raises(ValueError, match="gate"):
        _form(_directional(gate=None))
