"""Pure maker/taker amount math for a Polymarket CLOB order — money-critical, exact Decimal.

A CLOB order trades USDC ↔ outcome tokens; both are 6-decimal base units on-chain. For a **BUY**
the maker pays USDC and receives tokens; for a **SELL** the maker pays tokens and receives USDC:

    BUY:  makerAmount = round6(price * size) USDC, takerAmount = round6(size) tokens
    SELL: makerAmount = round6(size) tokens,        takerAmount = round6(price * size) USDC

``price`` is USDC per token in ``(0, 1]``; ``size`` is the number of shares. Amounts are returned
as integers in 6-decimal base units (``* 1_000_000``). Rounding is half-up on the 6th decimal —
the live API rejects sub-µ-USDC dust, so we quantize before scaling (the value matches what the
on-chain settlement expects). Verify the exact rounding/tick rules against the live API before
enabling real submission.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
SCALE = Decimal(10) ** 6  # USDC + outcome tokens are 6 decimals on Polygon
_Q6 = Decimal("0.000001")


def _to_base_units(amount: Decimal) -> int:
    """Quantize to 6 decimals (half-up), then scale to an integer base-unit amount."""
    return int((amount.quantize(_Q6, rounding=ROUND_HALF_UP) * SCALE).to_integral_value())


def order_amounts(side: str, price: Decimal, size: Decimal) -> tuple[int, int]:
    """``(maker_amount, taker_amount)`` in 6-decimal base units for ``side`` ∈ {"BUY","SELL"}.

    ``price`` must be in ``(0, 1]`` and ``size`` > 0 (an order moves a positive quantity)."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if not (ZERO < price <= ONE):
        raise ValueError(f"price must be in (0, 1], got {price}")
    if size <= ZERO:
        raise ValueError(f"size must be > 0, got {size}")

    usdc = price * size
    if side == "BUY":
        return _to_base_units(usdc), _to_base_units(size)
    return _to_base_units(size), _to_base_units(usdc)
