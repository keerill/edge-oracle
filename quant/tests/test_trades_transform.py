"""Pure trade transform — the single raw->canonical money-coercion site for trade prints.

Pins: Decimal comes from the exact wire literal (never via float), timestamp -> UTC datetime,
side/trade_id carried through, and the caller-supplied market_id (token->market mapping) lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.ingestion.trades_transform import trade_from_raw
from app.polymarket.schemas import RawTrade


def _raw(**over) -> RawTrade:
    base = dict(
        asset="98578534202029301876734952437102892680132626239125586048359740351496370265187",
        conditionId="0x5ed1",
        side="BUY",
        size="30.864194",
        price="0.8099999954639995",
        timestamp=1781681893,
        transactionHash="0xabc",
        outcome="Down",
        outcomeIndex=1,
    )
    base.update(over)
    return RawTrade(**base)


def test_trade_from_raw_coerces_exact_decimals_and_time():
    t = trade_from_raw(_raw(), market_id="m1")
    assert t.token_id == _raw().asset
    assert t.market_id == "m1"
    assert t.taker_side == "BUY"
    assert t.trade_id == "0xabc"
    # exact wire literal -> Decimal, no float rounding
    assert t.price == Decimal("0.8099999954639995")
    assert t.size == Decimal("30.864194")
    assert t.time == datetime(2026, 6, 17, 7, 38, 13, tzinfo=UTC)


def test_price_is_not_float_mediated():
    # The exact literal must be preserved (a float round-trip would not equal this Decimal).
    t = trade_from_raw(_raw(price="0.123456789012345678"), market_id="m1")
    assert t.price == Decimal("0.123456789012345678")
    assert t.price != Decimal(0.123456789012345678)  # Decimal(float) would differ


def test_missing_side_is_none():
    t = trade_from_raw(_raw(side=None), market_id="m1")
    assert t.taker_side is None
