"""Per-category Polymarket taker fee — pure, Decimal-native (SPEC §6).

    φ_cat(p) = feeRate_cat · (p·(1−p))^exp_cat     # per-dollar fee rate, peaks at p=0.5
    fee_per_share(p) = p · φ_cat(p)                # absolute fee for one share at price p

Rates are encoded from the published schedule and cross-checked to every peak effective rate
(the worked examples in test_fees.py). An unknown/absent category falls back to the **most
conservative** (crypto) rate, so we never under-estimate cost. No I/O, no float in the money path
(the only non-rational step is the exp=0.5 square root, computed in a fixed Decimal context).
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from pydantic import BaseModel, ConfigDict

ZERO = Decimal(0)
ONE = Decimal(1)


class FeeParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    fee_rate: Decimal
    exp: Decimal  # 1 (linear) or 0.5 (sqrt) per the published schedule


# Canonical category -> fee params. ``unknown`` (the default) mirrors crypto: the most
# conservative non-zero rate, so an uncategorized market is never under-charged.
FEE_TABLE: dict[str, FeeParams] = {
    "crypto": FeeParams(fee_rate=Decimal("0.072"), exp=ONE),
    "politics": FeeParams(fee_rate=Decimal("0.040"), exp=ONE),
    "finance": FeeParams(fee_rate=Decimal("0.040"), exp=ONE),
    "sports": FeeParams(fee_rate=Decimal("0.030"), exp=ONE),
    "economics": FeeParams(fee_rate=Decimal("0.030"), exp=Decimal("0.5")),
    "geopolitical": FeeParams(fee_rate=ZERO, exp=ONE),
}
_DEFAULT = FEE_TABLE["crypto"]  # most-conservative fallback for unknown/None


def params_for(category: str | None) -> FeeParams:
    """Fee params for a category (case-insensitive); the conservative default when unknown."""
    if category is None:
        return _DEFAULT
    return FEE_TABLE.get(category.strip().casefold(), _DEFAULT)


def _check_price(price: Decimal) -> None:
    if not (ZERO <= price <= ONE):
        raise ValueError(f"price must be in [0, 1], got {price}")


def _shape(base: Decimal, exp: Decimal) -> Decimal:
    """``base ** exp`` for the two published exponents (1 -> identity, 0.5 -> sqrt)."""
    if exp == ONE:
        return base
    if exp == Decimal("0.5"):
        with localcontext() as ctx:  # deterministic precision for the sqrt
            ctx.prec = 50
            return +base.sqrt()
    raise ValueError(f"unsupported fee exponent {exp}")  # only 1 and 0.5 are scheduled


def phi(price: Decimal, category: str | None) -> Decimal:
    """The per-dollar fee rate ``φ_cat(price)`` — peaks at price=0.5, zero at the extremes."""
    _check_price(price)
    p = params_for(category)
    if p.fee_rate == ZERO:
        return ZERO
    return p.fee_rate * _shape(price * (ONE - price), p.exp)


def fee_per_share(price: Decimal, category: str | None) -> Decimal:
    """Absolute taker fee for one share bought/sold at ``price`` = ``price · φ_cat(price)``."""
    return price * phi(price, category)
