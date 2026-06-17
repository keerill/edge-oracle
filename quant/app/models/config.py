"""User config — the personal bankroll & risk-appetite knobs the dashboard owns.

A single persisted row (``store.load_user_config`` / ``upsert_user_config``) that overrides
the static ``Settings`` defaults so sizing is personal to the operator and survives restarts.
Frozen, ``Decimal``-native, range-validated. When no row exists yet the API falls back to the
env defaults via :meth:`UserConfig.from_settings`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from app.config import Settings

ZERO = Decimal(0)
ONE = Decimal(1)


class UserConfig(BaseModel):
    """Personal sizing / risk preferences. ``bankroll`` is in USD; the rest are fractions in
    ``[0, 1]``. ``risk_threshold`` is the maximum probability of loss the operator tolerates
    on a single bet (used to filter the personalized views)."""

    model_config = ConfigDict(frozen=True)

    bankroll: Decimal
    kelly_frac: Decimal  # fractional Kelly applied to every sized bet
    kelly_cap: Decimal  # hard per-position cap (fraction of bankroll)
    corr_cap_frac: Decimal  # per-tag exposure cap (fraction of bankroll)
    risk_threshold: Decimal  # max acceptable probability of loss per bet, in [0, 1]

    @field_validator("bankroll")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < ZERO:
            raise ValueError("bankroll must be >= 0")
        return v

    @field_validator("kelly_frac", "kelly_cap", "corr_cap_frac", "risk_threshold")
    @classmethod
    def _fraction(cls, v: Decimal) -> Decimal:
        if not (ZERO <= v <= ONE):
            raise ValueError("fraction must be in [0, 1]")
        return v

    @classmethod
    def from_settings(cls, settings: Settings) -> UserConfig:
        """The default config from the static ``Settings`` (used when no row is persisted).
        ``risk_threshold`` defaults to 1 — surface everything until the operator narrows it."""
        return cls(
            bankroll=settings.backtest_initial_bankroll,
            kelly_frac=settings.kelly_frac,
            kelly_cap=settings.kelly_cap,
            corr_cap_frac=settings.corr_cap_frac,
            risk_threshold=ONE,
        )
