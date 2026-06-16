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

## Slice: Set-arb signal scanner (YES/NO rebalancing)  ✅ done (2026-06-16)

First signals slice: a standalone scanner that finds complete-set *rebalancing arbitrage*
across the tracked universe and records opportunities to a new `signals` table. A complete
set (1 YES + 1 NO) redeems for exactly $1.00, so a dislocated book opens two risk-free edges:
**LONG** (buy YES+NO < $1, redeem for $1) and **SHORT** (mint a set for $1 via Split, sell
YES+NO > $1). **Advisor only — detects + records, never executes.**

### What's done
- **Pure math** (`app/math/arb.py`): `vwap_to_fill` (size-weighted avg to fill a target qty
  by walking levels; reports `fully_filled`), `evaluate_long_set`/`evaluate_short_set`/
  `evaluate_market`, and `ArbParams`. No I/O, no clock, no Settings.
- **Signal model** (`app/models/signal.py`): frozen, `Decimal`-native `ArbSignal` (`kind`,
  the two VWAP prices, `set_size`, gross/costs/net edge, hypothetical P&L).
- **Schema**: `signals` *regular* table (`app/db/tables.py`) + Alembic `0002_signals` (no PK
  like `quotes`; indexed by `time` and `(market_id, time)`; unbounded NUMERIC).
- **Store** (`app/ingestion/store.py`): `insert_signals` (batch append) + reusable
  `load_tracked_markets` (the read counterpart to `upsert_markets`).
- **Scanner** (`app/signals/engine.py`): `run_signal_scan_once` (timing-free test seam)
  reloads the universe, fetches both books per market, evaluates, persists. Per-token +
  per-market isolation. `run_signal_poller` loop + CLI `python -m app.signals.engine [once|loop]`.
- **Config** (`app/config.py`): `EDGE_ARB_{SET_SIZE,GAS,SLIPPAGE,MIN_NET_EDGE}` (`Decimal`).
- **Tests**: **+22** (17 pure-arb worked examples + 3 engine orchestration + 2 DB-gated store).

### Verified
- `cd quant && uv run pytest -q` → **67 passed, 6 skipped** (store skipped without a DB).
- With `EDGE_TEST_DATABASE_URL=…edge_test` → **73 passed** (incl. the signals NUMERIC↔Decimal
  round-trip and tracked-only reload).
- `uv run alembic upgrade head` → `0001_init → 0002_signals`; `signals` table + both indexes
  confirmed via `\d signals`.
- **Live smoke** `uv run python -m app.signals.engine once` → loaded 5 tracked markets,
  fetched 10 books (all 200 OK), wrote **0** signals (no real edge past the 2c gate — expected).

### Money-math / correctness decisions (carry forward)
- LONG: `gross = 1.00 − (YES_ask + NO_ask)`; SHORT: `gross = (YES_bid + NO_bid) − 1.00`.
  Prices are the **executed VWAP** (ask paid / bid received), never the midpoint.
- **Costs ARE the threshold**: `costs = gas + slippage` (default 2c); flag only when
  `net = gross − costs` *strictly exceeds* `min_net_edge` (default 0). `pnl = net × set_size`.
- Signal only when **both legs fully fill** at `set_size` (a too-thin book is rejected).
- LONG/SHORT are mutually exclusive (bid ≤ ask ⇒ Σbid ≤ Σask), so a market yields ≤ 1 signal.
- Worked example reproduced in tests: `0.46 + 0.49 = 0.95` → 5c gross, **3c net** after 2c costs.

### Decisions locked this slice
- **Standalone scanner** (re-fetches books); **zero changes to the ingestion poller**.
  Reusing the ingestion cycle's books is a deferred optimization (see What's next).
- `signals` is a **regular table** (sparse, append-only), not a hypertable; one row per
  flagged market per scan (no dedup yet).

## Slice: Price-based signals (favourite-longshot + extreme correction)  ✅ done (2026-06-16)

Two price-only heuristic signals as **pure, tested functions**, both recorded to the same
`signals` table under a new `strategy` tag. Favourite-longshot exploits the documented bias
(back the underpriced favourite, fade the overpriced longshot); extreme-correction nudges an
extreme implied probability back toward 0.50 and exposes the corrected estimate as a future
fair-value input. **Advisor only — math + persistence; no sizing, no live scanner this slice.**

### What's done
- **Pure math**: `app/math/longshot.py` (`evaluate_favourite_longshot` + `LongshotParams`) and
  `app/math/correction.py` (`evaluate_extreme_correction` + `CorrectionParams`). `Decimal` in,
  signal/`None` out; no I/O, no clock (capture time injected). Thresholds are frozen-`Params`
  defaults (not yet `EDGE_*` knobs — those land with the scanner, as the arb knobs did).
- **Signal models** (`app/models/signal.py`): frozen `FavouriteLongshotSignal` (`kind`
  buy_yes/buy_no, `price`, `edge_score`) and `ExtremeCorrectionSignal` (`price`, `fair_value`),
  plus a `Signal` union. Each maps to a subset of the `signals` columns.
- **Schema**: generalized `signals` (`app/db/tables.py`) + Alembic `0003_price_signals` — added
  `strategy` (NOT NULL, server_default `'set_arb'`), relaxed the 7 set-arb NUMERIC columns to
  nullable, added nullable `price`/`edge_score`/`fair_value`, added `ix_signals_strategy_time`.
- **Store** (`app/ingestion/store.py`): `insert_signals` broadened to the `Signal` union (one
  homogeneous strategy per call); set-arb path untouched (its insert omits `strategy` → default).
- **Tests**: **+33** (17 favourite-longshot + 14 correction worked examples covering every
  boundary; +2 DB-gated store roundtrips for the new strategies).

### Verified
- `cd quant && uv run pytest -q` → **98 passed, 8 skipped** (store skipped without a DB).
- With `EDGE_TEST_DATABASE_URL=…edge_test` → **106 passed** (incl. both new strategy roundtrips:
  strategy tag persists, unused set-arb columns are NULL, NUMERIC↔Decimal exact).
- `uv run alembic upgrade head` → `0002_signals → 0003_price_signals`; `\d signals` confirms the
  new columns, the now-nullable arb columns, and `ix_signals_strategy_time`. Downgrade→upgrade
  round-trip clean (arb columns restored to NOT NULL).

### Money-math / correctness decisions (carry forward)
- **Bands** (favourite-longshot, closed intervals): back YES on `0.75 ≤ m ≤ 0.92`, buy NO on
  `0.05 ≤ m ≤ 0.15`; the gaps and the true extremes (`<0.03` / `>0.97`) emit nothing (spread eats
  the edge). Boundaries: `m=0.04 → none`, `0.10 → buy_no score 0.5`, `0.80 → buy_yes score
  ≈0.2941`, `0.96 → none`.
- **Edge score** ∈ [0,1], monotonic toward the more-mispriced end: favourite `(m−0.75)/0.17`,
  longshot `(0.15−m)/0.10`. A heuristic strength, **not** a probability/EV.
- **Correction** (open band `m<0.15` or `m>0.85`): the nudge is **absolute** percentage points,
  scaled by distance from 0.50 — `0.03` at the band edge → `0.08` at the extreme (clamped);
  `corrected = m ± nudge` toward 0.50, never overshoots. Worked: `0.04 → 0.1067`, `0.10 → 0.1467`,
  `0.96 → 0.8933`, `0.0 → 0.08`, `1.0 → 0.92`. Both functions raise on `m ∉ [0,1]`.
- One `signals` table, `strategy`-tagged; set-arb and price-signal columns are mutually exclusive
  per row (all nullable). No JSON blob — money stays Decimal↔NUMERIC.

### Decisions locked this slice
- **Math + persistence only** — no Kelly sizing, no `EDGE_*` knobs, and no live scanner/poller yet
  (wiring `m` from the latest `quotes` midpoint on a loop is the next slice, mirroring how
  `signals/engine.py` followed `math/arb.py`). The write path is proven by the DB-gated roundtrips.

## What's next
- **Trades ingestion**: Data API `/trades` client + `trades` hypertable + poll trade prints.
- **Category resolution**: Gamma `/markets` frequently omits `category` (observed NULL in the
  smoke run). Derive it from `events[].tags[]` so the fee table (crypto/politics/…) can key on it.
- **More signals math**: Kelly sizing (fractional + hard cap), fee-by-category table, model
  fair-value + CI-lower-bound gate. (Set-arb `math/arb.py` + `signals/engine.py` done; price
  signals `math/longshot.py` + `math/correction.py` done.)
- **Price-signal scanner**: wire the two new pure functions to a live scan — read the latest
  midpoint per market from `quotes`, evaluate, and persist on a loop with a CLI (mirrors
  `signals/engine.py`); add the `EDGE_*` knobs for their bands/nudge then. Feed `fair_value`
  into the future fair-value/Kelly gate.
- **Signals plumbing**: publish signals to Redis + `GET /signals`; merge the signal scan into
  the ingestion cycle to **reuse the books it already fetches** (drop the standalone re-fetch).
- **Backtest**: deterministic replay over the stored `quotes` ticks.
- **Web**: Next.js BFF + dashboard (separate slice).

## Open questions / observations
- `category` often absent from Gamma `/markets` (see above) — resolve before the fee logic needs it.
- Gamma discovery is single-page (limit 500). Fine for `top_n ≤ 500`; add offset paging if the
  tracked universe ever needs to exceed that.
- Discovery refreshes every `discovery_interval_s` (default 300s); tune once running continuously.
- Live `quotes`/`markets` rows from the smoke run remain in the `edge` DB (dev data; wipe with
  `docker compose ... down -v`). `edge_test` DB exists for the store integration tests.
- Set-arb sizing is fixed at `set_size=1` (per-unit-set edge). Depth-maximizing sizing (how many
  sets stay profitable as the VWAP climbs) is deferred — it pairs with the Kelly/backtest slices.
- The signal scan re-fetches books the ingestion poller already pulled; fine at `top_n ≤ 50`, but
  merge the two loops before scaling the universe to avoid doubling CLOB load.
