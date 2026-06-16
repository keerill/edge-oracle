"""Runtime configuration (pydantic-settings). Single source of truth.

All values are overridable via ``EDGE_``-prefixed environment variables or a local
``.env`` file. Most knobs are endpoints, cadences, and HTTP/backoff; the set-arb
scanner adds a few money knobs (gas/slippage/threshold) — kept as ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_", env_file=".env", extra="ignore"
    )

    # --- Database -------------------------------------------------------------
    # Default matches infra/docker-compose.yml. Always an asyncpg URL.
    database_url: str = "postgresql+asyncpg://edge:edge@localhost:5432/edge"
    # Separate DB for the store integration test; when unset that test is skipped.
    test_database_url: str | None = None

    # --- Universe / cadence ---------------------------------------------------
    top_n: int = 50
    # Comma-separated condition ids. When non-empty, overrides top-N discovery
    # and restricts the universe to exactly these markets. See ``allowlist_ids``.
    condition_id_allowlist: str = ""
    scan_interval_s: float = 15.0
    discovery_interval_s: float = 300.0
    gamma_page_limit: int = 500  # Gamma caps /markets limit at 500

    # --- Polymarket endpoints -------------------------------------------------
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    # CLOB market WebSocket (live order-book deltas). Public, no auth for the market channel.
    clob_ws_url: str = "wss://ws-subscribe.clob.polymarket.com/ws/market"

    # --- Streaming (live arb -> Redis pub/sub -> SSE) -------------------------
    # The live stream re-runs the set-arb math on every book delta and publishes high-net-edge
    # signals to a Redis channel; the web SSE endpoint subscribes and fans out to the dashboard.
    redis_url: str = "redis://localhost:6379"
    signals_channel: str = "edge:signals"

    # --- Set-arb signal scanner ----------------------------------------------
    # ``costs`` (gas + slippage) IS the flag threshold; an opportunity fires only when
    # net = gross - costs strictly exceeds ``arb_min_net_edge`` (an extra gate, off by
    # default). Money knobs are Decimal (env strings parse exactly: EDGE_ARB_GAS=0.015).
    arb_set_size: Decimal = Decimal(1)  # complete sets (1 YES + 1 NO) to price each edge for
    arb_gas: Decimal = Decimal("0.01")  # per-set on-chain cost estimate (split/merge/redeem)
    arb_slippage: Decimal = Decimal("0.01")  # per-set buffer beyond modeled book depth
    arb_min_net_edge: Decimal = Decimal(0)  # extra profit gate; flag only when net > this

    # --- Backtest harness ----------------------------------------------------
    # Pure mirror lives in app.models.backtest.BacktestParams; the engine maps these on.
    # Money knobs are Decimal (env strings parse exactly: EDGE_BACKTEST_INITIAL_BANKROLL=5000).
    backtest_initial_bankroll: Decimal = Decimal(1000)
    kelly_frac: Decimal = Decimal("0.25")  # fractional Kelly applied to every sized bet
    kelly_cap: Decimal = Decimal("0.05")  # hard per-position cap (fraction of bankroll)
    corr_cap_frac: Decimal = Decimal("0.05")  # per-tag exposure cap (fraction of bankroll)
    model_error_margin: Decimal = Decimal("0.05")  # p_lo = p_side - this (CI lower bound)
    mc_sigma: Decimal = Decimal("0.05")  # std-dev of the Monte-Carlo model-error perturbation
    mc_sims: int = 1000  # Monte-Carlo simulation count
    mc_seed: int = 12345  # Monte-Carlo RNG seed (determinism)
    # Optional path to a JSON market-outcome feed for GET /backtest (resolution ingestion is a
    # later slice). Unset -> the endpoint returns a well-formed zero-bet report.
    backtest_resolutions_path: str | None = None

    # --- HTTP / backoff / throttle -------------------------------------------
    http_timeout_s: float = 10.0
    max_retries: int = 5
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 30.0
    backoff_jitter: bool = True
    max_concurrency: int = 8

    # --- Lifespan -------------------------------------------------------------
    # When False (default), the FastAPI app does NOT start the poller; run it via
    # ``python -m app.ingestion.scanner`` instead. Set True to poll on startup.
    run_poller_on_startup: bool = False

    # --- Observability: structured logging -----------------------------------
    # Every entrypoint calls observability.logging.configure_logging(service). JSON to
    # stdout by default; EDGE_LOG_JSON=false restores the human format for local tailing.
    log_level: str = "INFO"
    log_json: bool = True

    # --- Observability: Sentry (errors) --------------------------------------
    # No-op unless EDGE_SENTRY_DSN is set. The DSN is a SECRET — supply it via env / a
    # secret manager, NEVER commit it. With LoggingIntegration, every logger.error /
    # logger.exception in the loops becomes a Sentry event automatically.
    sentry_dsn: str | None = None
    sentry_environment: str = "dev"

    # --- Observability: Prometheus metrics -----------------------------------
    # The FastAPI app serves /metrics; each standalone CLI exposes its own endpoint on
    # EDGE_METRICS_PORT. Running several CLIs on one host needs distinct ports (9100 poller,
    # 9101 signals, 9102 streaming, 9103 monitor) — a bind clash is logged, never fatal.
    metrics_enabled: bool = True
    metrics_port: int = 9100

    # --- Observability: alerts -----------------------------------------------
    # The three named alerts (WS drop / drawdown breach / calibration drift) are published to
    # this Redis channel (web SSE -> dashboard toast) and captured to Sentry. Thresholds are
    # Decimal fractions (exact). The monitor loop evaluates drawdown + drift every cadence; the
    # WS-drop alert is event-driven in the streaming engine.
    alerts_channel: str = "edge:alerts"
    drawdown_alert_threshold: Decimal = Decimal("0.20")  # peak-to-trough fraction
    calibration_drift_threshold: Decimal = Decimal("0.05")  # claimed-realized gap
    ws_drop_alert_threshold: int = 1  # reconnects before alerting (1 = any drop)
    monitor_interval_s: float = 60.0

    @property
    def allowlist_ids(self) -> tuple[str, ...]:
        """Parse the comma-separated allowlist into a tuple of condition ids."""
        return tuple(s.strip() for s in self.condition_id_allowlist.split(",") if s.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (cleared in tests via ``get_settings.cache_clear()``)."""
    return Settings()
