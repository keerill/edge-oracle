"""Execution-module configuration (pydantic-settings). ``EDGE_EXEC_``-prefixed, mirroring the
advisor's ``quant/app/config.py`` discipline — money knobs are ``Decimal`` (env strings parse
exactly). This is the composition root: it builds the breaker limits and parses the allowlists.

SECURITY: ``enabled`` defaults **false** (the hard CLAUDE.md gate — nothing trades until turned
on). No secrets live here: the (later) KMS key handle is a non-secret id; KMS credentials / relay
auth are injected via env by a secret manager, never committed (same discipline as the advisor's
``sentry_dsn``). The executor uses its OWN database (``edge_exec``), separate from the advisor's —
the advisor DB role must have no grants on the ``exec_*`` tables.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.breakers.checks import BreakerLimits


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_EXEC_", env_file=".env", extra="ignore"
    )

    # --- Master switch (default OFF — the hard gate) --------------------------
    enabled: bool = False
    # Dry-run: run the FULL pipeline (form → breakers → approval → sign) but never broadcast —
    # the submit step records what WOULD be sent and stops. Default TRUE: nothing reaches a
    # network until explicitly turned off (and live submission additionally needs ``enabled``).
    dry_run: bool = True
    # Semi-auto: require a human approval token for EVERY trade (not just above the threshold).
    # The operator chose manual confirmation per trade; this is the pipeline-level gate.
    require_approval_for_all: bool = True

    # --- Database (the executor's OWN db, isolated from the advisor) ----------
    database_url: str = "postgresql+asyncpg://edge:edge@localhost:5432/edge_exec"
    test_database_url: str | None = None  # EDGE_EXEC_TEST_DATABASE_URL; store tests skip when unset

    # --- Circuit breakers (Decimal money knobs) ------------------------------
    hot_wallet_cap_usd: Decimal = Decimal("500")  # max a single trade may move from the hot float
    per_trade_cap_usd: Decimal = Decimal("100")
    approval_threshold_usd: Decimal = Decimal("50")  # above this, a human must approve
    rate_limit_count: int = 10  # trades per window
    rate_limit_window_s: float = 60.0
    rate_limit_notional_usd: Decimal = Decimal("1000")  # cumulative $ per window (anti split-bypass)
    max_slippage: Decimal = Decimal("0.01")

    # --- Chain / endpoints (used by later phases) ----------------------------
    chain_id: int = 137  # Polygon
    clob_api_base: str = "https://clob.polymarket.com"
    relay_url: str = ""  # Flashbots-style private relay (Phase 5)
    signer_url: str = ""  # internal signer service (Phase 4)
    kms_key_id: str = ""  # non-secret AWS KMS handle (production signer)
    # TESTNET-ONLY local signing key (Phase 4 offline). A SECRET — inject via a secret manager,
    # NEVER commit, and NEVER set on mainnet (production uses kms_key_id, key never exported).
    signer_private_key: str | None = None
    # Secret the signer uses to verify approval-token HMACs (shared with the approval UI). SECRET.
    approval_secret: str = ""
    approval_token_ttl_s: int = 300

    # --- Allowlists (csv -> frozenset via the properties below) --------------
    # Enforced in the breakers AND (later) independently in the signer.
    allowlist_contracts: str = ""  # target contracts (CLOB Exchange / NegRisk / CTF / USDC)
    allowlist_spenders: str = ""  # erc20 approve spenders
    allowlist_withdrawals: str = ""  # permitted withdrawal recipients

    # --- Redis (consume advisor signals; publish exec events) ----------------
    redis_url: str = "redis://localhost:6379"
    signals_channel: str = "edge:signals"  # consumed (advisor output)
    exec_intents_channel: str = "edge:exec:intents"
    exec_approvals_channel: str = "edge:exec:approvals"

    # --- Observability --------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    metrics_port: int = 9110  # signer would use 9111

    @staticmethod
    def _csv(value: str) -> frozenset[str]:
        return frozenset(s.strip() for s in value.split(",") if s.strip())

    @property
    def allowlisted_contracts(self) -> frozenset[str]:
        return self._csv(self.allowlist_contracts)

    @property
    def allowlisted_spenders(self) -> frozenset[str]:
        return self._csv(self.allowlist_spenders)

    @property
    def allowlisted_withdrawals(self) -> frozenset[str]:
        return self._csv(self.allowlist_withdrawals)

    def breaker_limits(self) -> BreakerLimits:
        """Project the settings onto the pure breaker limits."""
        return BreakerLimits(
            enabled=self.enabled,
            per_trade_cap_usd=self.per_trade_cap_usd,
            max_slippage=self.max_slippage,
            rate_limit_count=self.rate_limit_count,
            rate_limit_notional_usd=self.rate_limit_notional_usd,
            hot_wallet_cap_usd=self.hot_wallet_cap_usd,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (cleared in tests via ``get_settings.cache_clear()``)."""
    return Settings()
