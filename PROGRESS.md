# PROGRESS

## Slice: Polymarket data ingestion → TimescaleDB  ✅ done (2026-06-16)

First end-to-end slice of EdgeOracle: stand up `quant/`, typed+validated Polymarket
clients, the canonical schema + TimescaleDB hypertables, and a scheduled poller that
snapshots top-of-book for an auto-discovered universe of liquid binary markets.
**Ingestion only** — no signals/math/sizing, no web/UI, no Redis, no trades.

### What's done
- **`quant/` service** (uv, Python 3.12, installed as the `app` package; FastAPI `/health`).
- **Untrusted boundary** (`app/polymarket/schemas.py`): raw Pydantic models for every
  Gamma/CLOB response, `extra="ignore"`, prices/ids kept as strings.
- **Pure transform** (`app/ingestion/transform.py`): the single raw→canonical,
  string→`Decimal` site. Parses stringified arrays, keeps uint256 token ids as `str`,
  derives `midpoint`/`spread`, ranks/selects the universe. No I/O, capture time injected.
- **Canonical models** (`app/models/`): frozen, `Decimal`-native `Market`/`OrderBook`/`QuoteSnapshot`.
- **HTTP core** (`app/polymarket/http.py`): shared async client + hand-rolled retry/backoff
  (429+5xx+transport, jitter, Retry-After; injectable sleep/rng).
- **Typed clients**: `GammaClient.list_active_markets`; `ClobClient.get_book` (+ deliverable
  `get_midpoint`/`get_spread`/`get_prices_history`, fixture-tested, not wired into the loop).
- **DB**: `app/db/tables.py` (`markets` + `quotes` hypertable, unbounded `NUMERIC`),
  async `app/db/engine.py`, Alembic async env (reuses asyncpg, **no psycopg**), migration
  `0001_init` (extension → tables → `create_hypertable` → time-series index).
- **Store** (`app/ingestion/store.py`): `upsert_markets` (ON CONFLICT), `set_untracked`,
  `insert_quotes` (batch). **`Decimal` ↔ NUMERIC end-to-end; no float money math.**
- **Poller** (`app/ingestion/scanner.py`): `run_scan_once` (timing-free test seam),
  `run_poller` (two cadences: snapshot every `scan_interval_s`, discovery every
  `discovery_interval_s`), failure isolation per token/market/tick. CLI `python -m
  app.ingestion.scanner [once|loop]`; optional FastAPI lifespan poller (`EDGE_RUN_POLLER_ON_STARTUP`).
- **Tests**: **51 passing** (47 offline fixtures-only + 4 DB-gated store). Offline suite never
  touches the live API.

### Verified
- `cd quant && uv run pytest -q` → 47 passed, 4 skipped (store skipped without a test DB).
- With `EDGE_TEST_DATABASE_URL=…edge_test` → **51 passed** (store integration incl. the
  NUMERIC↔Decimal round-trip guard).
- `docker compose -f infra/docker-compose.yml up -d` + `cd quant && uv run alembic upgrade head`
  → `quotes` hypertable created (confirmed via `timescaledb_information.hypertables`).
- **Live smoke** `EDGE_TOP_N=5 uv run python -m app.ingestion.scanner` → discovered 5 markets,
  stored 10 quotes; derived mid/spread exact (e.g. bid 0.84 / ask 0.88 → mid 0.86, spread 0.04).
- `uv run uvicorn app.main:app` → `GET /health` `{"status":"ok"}`.

### Run / verify commands
```
docker compose -f infra/docker-compose.yml up -d           # Postgres+TimescaleDB (+Redis)
cd quant && uv sync                                          # env
cd quant && uv run alembic upgrade head                     # schema + hypertable
cd quant && uv run pytest -q                                # primary check (offline)
EDGE_TEST_DATABASE_URL=postgresql+asyncpg://edge:edge@localhost:5432/edge_test \
  uv run pytest -q                                          # incl. store integration
EDGE_TOP_N=5 uv run python -m app.ingestion.scanner         # one live scan cycle
cd quant && uv run uvicorn app.main:app --reload            # /health
```
Config is `EDGE_`-prefixed (see `app/config.py`): `EDGE_DATABASE_URL`, `EDGE_TOP_N`,
`EDGE_CONDITION_ID_ALLOWLIST` (csv), `EDGE_SCAN_INTERVAL_S`, `EDGE_DISCOVERY_INTERVAL_S`,
`EDGE_MAX_RETRIES`/`EDGE_BACKOFF_*`, `EDGE_RUN_POLLER_ON_STARTUP`.

### Money-math / correctness decisions (carry forward)
- `Decimal` is constructed **from the wire string**, never `Decimal(float)`; the only
  legitimate float is `prices-history.p`, which is never stored.
- `midpoint=(best_bid+best_ask)/2`, `spread=best_ask−best_bid`, **`None` when a side is
  empty** (never fabricate a price). Derived from one `/book` call per token (atomic).
- Best bid/ask chosen defensively (max bid / min ask), tolerant of upstream re-ordering.
- Token/condition ids are uint256 → always `str` (verified no precision loss).
- NUMERIC columns are **unbounded** (no precision/scale) to avoid silent rounding.

### Decisions locked this slice
- Universe = **top-N by liquidity** auto-discovered (default 50), optional condition-id allowlist.
- **Quotes only** (trades deferred). `outcomes` match is casefold-tolerant `["yes","no"]`.
- Migrations use Alembic's **async env over asyncpg** (no psycopg dependency added).
- No deps added beyond the SPEC §9 approved list (fastapi/uvicorn are the named framework).

## What's next
- **Trades ingestion**: Data API `/trades` client + `trades` hypertable + poll trade prints.
- **Category resolution**: Gamma `/markets` frequently omits `category` (observed NULL in the
  smoke run). Derive it from `events[].tags[]` so the fee table (crypto/politics/…) can key on it.
- **Signals slice**: `math/` (kelly, arb, slippage, gate, fees) + `signals/engine.py` consuming
  the full books the scanner already fetches; publish to Redis + `GET /signals`.
- **Backtest**: deterministic replay over the stored `quotes` ticks.
- **Web**: Next.js BFF + dashboard (separate slice).

## Open questions / observations
- `category` often absent from Gamma `/markets` (see above) — resolve before the fee logic needs it.
- Gamma discovery is single-page (limit 500). Fine for `top_n ≤ 500`; add offset paging if the
  tracked universe ever needs to exceed that.
- Discovery refreshes every `discovery_interval_s` (default 300s); tune once running continuously.
- Live `quotes`/`markets` rows from the smoke run remain in the `edge` DB (dev data; wipe with
  `docker compose ... down -v`). `edge_test` DB exists for the store integration tests.
