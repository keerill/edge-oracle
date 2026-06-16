"""Runtime configuration (pydantic-settings). Single source of truth.

All values are overridable via ``EDGE_``-prefixed environment variables or a local
``.env`` file. No money values flow through config in the ingestion slice — only
endpoints, cadences, and HTTP/backoff knobs.
"""

from __future__ import annotations

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

    @property
    def allowlist_ids(self) -> tuple[str, ...]:
        """Parse the comma-separated allowlist into a tuple of condition ids."""
        return tuple(s.strip() for s in self.condition_id_allowlist.split(",") if s.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (cleared in tests via ``get_settings.cache_clear()``)."""
    return Settings()
