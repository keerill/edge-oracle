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

## Slice: Bet-sizing (fractional Kelly + caps)  ✅ done (2026-06-16)

The money-correctness core: pure position-sizing math — the Kelly fraction, fractional Kelly
with a hard cap, the cost/edge gate, the bankroll→stake pipeline, and a per-tag correlation cap
(one macro theme = one bet). **Advisor only — math that proposes a stake; no execution, no
wallet, no persistence this slice.** Tests are the spec (TDD: written first, watched fail).

### What's done
- **Pure math** (`app/math/bet_sizing.py`): `kelly_fraction(p, m) = (p−m)/(1−m)` (0 on no edge),
  `fractional_kelly(p, m, frac=0.25, cap=0.05) = min(frac·kelly, cap)` floored at 0,
  `edge_gate(p_lo, m, half_spread, slippage, gas)` (strict `>`), `position_size(...)` (gate →
  fractional Kelly on the ask → ×bankroll), and `cap_correlated_stakes(positions, max_per_tag)`
  over a frozen `TaggedStake(tag, stake)`. `Decimal` in/out; no I/O, no clock, no `Settings`.
- **Tests**: **+36** worked examples (every branch + boundary + out-of-range guard).

### Verified
- `cd quant && uv run pytest tests/test_bet_sizing.py -q` → **36 passed**.
- `cd quant && uv run pytest -q` → **134 passed, 8 skipped** (was 98+8; +36 new, no regressions).
- Anchor numbers reproduced by hand: `p=0.55, m=0.40 → kelly 0.25`, quarter 0.0625 **capped 0.05**;
  the 1c-edge-vs-2c-half-spread gate returns **False**; pro-rata `300·500/600 = 250` exact.

### Money-math / correctness decisions (carry forward)
- Defaults are **`Decimal`** literals (`0.25`/`0.05`), never float. Stake ∈ `[0, bankroll·cap]`.
- **Gate on `p_lo`** (CI lower bound), **size Kelly on `p`** (your estimate), at the **ask you pay
  `= m + half_spread`** (`slippage`+`gas` live in the gate only). Gate is **strict `>`**
  (break-even rejected).
- Correlation cap is **pro-rata** per tag to a **dollar** `max_per_tag` (caller passes
  `cap·bankroll`); the scale is `stake·max_per_tag/total` (**multiply-before-divide**, so exact
  ratios stay exact); preserves input order; an under-cap / 0-sum group is untouched.
- **Model-error margin** is folded into a conservative `p_lo` by the caller, not a separate
  `edge_gate` arg (keeps the gate signature minimal); can promote to a knob with the scanner.

### Decisions locked this slice
- **Sizing math only** — no `EDGE_*` knobs, no signal model, no DB/migration, no scanner wiring
  (mirrors how `math/longshot.py`+`math/correction.py` shipped before their scanner). `frac`/`cap`
  are function defaults; they become `EDGE_*` knobs when a sizing scanner/endpoint lands.

## Slice: Calibration journal (proving the probabilities are real)  ✅ done (2026-06-16)

How we hold the models honest: when a market resolves, journal `(estimate p, market price
m, outcome 0/1, strategy, ts)`, then score the journal — Brier + log-loss (overall and per
strategy tag), a decile reliability curve (claimed vs realized), and a **conservative,
shrink-only** Kelly-fraction suggestion when the model is overconfident in its
high-confidence bins. **Advisor only — math + persistence; no resolution-watcher, no
endpoint, no producer wiring this slice.** Tests are the spec (TDD: written first, watched
fail).

### What's done
- **Pure math** (`app/math/calibration.py`): `brier_score`, `log_loss`,
  `reliability_curve`, `suggest_kelly_fraction`, `summarize`, plus a frozen
  `CalibrationParams` (knobs: `n_bins`, `eps`, `high_confidence_threshold`, `base_frac`,
  `min_multiplier`, `ln_prec`, all range-validated). `Decimal` in, frozen models out; no
  I/O, no clock, no `Settings`.
- **Models** (`app/models/calibration.py`): frozen `CalibrationRecord` (the journal row;
  `estimate`/`price` in [0,1], `outcome: Literal[0,1]`) + result models `ReliabilityBin`,
  `CalibrationMetrics`, `KellyAdjustment`, `CalibrationSummary`.
- **Schema**: `calibration` *regular* table (`app/db/tables.py`) + Alembic
  `0004_calibration` — `estimate`/`price` unbounded NUMERIC, `outcome` SmallInteger +
  `ck_calibration_outcome` CHECK, `strategy` NOT NULL (no server_default), `ix_calibration_time`
  + `ix_calibration_strategy_time`. No PK / no hypertable (mirrors `signals`).
- **Store** (`app/ingestion/store.py`): `insert_calibration` (batch) + `load_calibration`
  (oldest-first, optional `strategy` filter) — the read/write seam for the scorer.
- **Tests**: **+16** (15 offline worked examples covering Brier/log-loss/reliability/Kelly/
  per-strategy + every guard; +1 DB-gated store roundtrip).

### Verified
- `cd quant && uv run pytest tests/test_calibration.py -q` → **15 passed**.
- `cd quant && uv run pytest -q` → **149 passed, 9 skipped** (was 134+8; +15 offline, +1
  DB-gated skip; no regressions).
- With `EDGE_TEST_DATABASE_URL=…edge_test` → **158 passed** (incl. the calibration roundtrip:
  `estimate`/`price` exact `Decimal`, `outcome` int 0/1, `strategy` persists, time-ordered
  load + strategy filter).
- `uv run alembic upgrade head` → `0003_price_signals → 0004_calibration`; `\d calibration`
  confirms the NOT-NULL columns (no `strategy` default), `numeric` money, `smallint`
  outcome, the CHECK, and both DESC indexes. `downgrade -1` drops it, `upgrade head`
  restores — clean roundtrip.

### Money-math / correctness decisions (carry forward)
- **Brier** = `mean((estimate − outcome)²)` — single-class binary, exact in `Decimal`.
  Anchors: all `p=0.5` → `0.25`; `[0.9,0.9,0.1,0.1]/[1,0,0,1]` → `0.41`.
- **Log-loss** = `−mean(y·ln(p)+(1−y)·ln(1−p))`, natural log via `Decimal.ln()`. `p` clipped
  **once** to `[eps, 1−eps]` (`eps=1e-12`, same clipped value in both terms) so `ln(0)`
  can't fire at `p∈{0,1}`; computed inside `localcontext(prec=50)` so the result doesn't
  depend on the caller's context. **No float, no `math.log`.** Anchors (quantized 6 dp):
  all `0.5` → `ln 2 = 0.693147`; the set → `−ln(0.09)/2 = 1.203973`; clip branch
  `p=0,outcome=1` → `12·ln 10 = 27.631021`.
- **Reliability**: equal-width deciles, `bin = min(int(estimate·n_bins), n_bins−1)`
  (`estimate` stays `Decimal` end-to-end; `1.0` → last/closed bin). **All** bins returned;
  empty bins are `count=0`, `claimed=realized=None` (never fabricate a frequency).
- **Kelly adjustment — shrink-only, aggregate**: over `estimate ≥ 0.7` (= bins 7–9),
  `multiplier = clamp(realized_avg/claimed_avg, 0, 1)`, `adjusted_frac = 0.25·multiplier`.
  **Invariant `adjusted_frac ≤ base_frac`** — underconfidence clamps to 1 (no change), it
  can never *raise* risk. Zero high-confidence records → all diagnostics `None` (no
  evidence ≠ calibrated). `worst_bin_multiplier` exposed as a diagnostic. Worked: ten
  `p=0.8`, six win → `0.6/0.8 = 0.75` → `adjusted_frac 0.1875`.
- **Per-strategy metrics are POOLED**, not mean-of-means (pinned by a split test: unequal
  groups give overall `0.1875` vs the wrong `0.125`).
- `outcome` is a **label, not money** → `SmallInteger` + CHECK; `estimate`/`price` stay
  unbounded NUMERIC↔Decimal. `strategy` is free-form `Text` (any producer tag), **no
  server_default** (a default would silently mislabel rows — unlike `signals`, calibration
  always supplies it).

### Decisions locked this slice
- **Math + persistence only** — no resolution-watcher/poller, no `GET /calibration`, no
  Redis, no producer wiring (mirrors how each math module shipped before its scanner). The
  store functions are the seam; the write path is proven by the DB-gated roundtrip.
- `CalibrationParams` knobs are **function defaults** (range-validated), not `EDGE_*` knobs
  yet — they graduate when a scoring endpoint/scanner lands.
- `calibration` is a **regular table** (one row per resolution; sparser than `signals`), no
  hypertable, no PK.

## Slice: Backtest harness over stored historical prices  ✅ done (2026-06-16)

Deterministic replay of the stored price history that finally answers "does following the
signals make money?" Replays `quotes` time-ordered (strict, no look-ahead), sizes each
signal with the existing Kelly module, simulates fills at the price actually payable (incl.
spread + slippage + gas), tracks bankroll event-driven, and reports return / hit rate /
max drawdown / Sharpe-like (overall + per strategy) — plus a Monte-Carlo pass that resamples
outcomes with a model-error perturbation for a final-bankroll distribution. **Advisor only —
math + a replay reader; outcomes are an explicit input (resolution ingestion is a later
slice).** Tests are the spec (TDD: written first, watched fail).

### What's done
- **Pure math** (`app/math/backtest.py`): `realized_pnl` (all costs baked into the
  directional fill; arb P&L is the outcome-independent `net_edge*set_size`), the metric
  helpers (`total_return`, `max_drawdown`, `hit_rate`, `sharpe_like`), `simulate` (the
  causal event loop), and `monte_carlo`. **Reuses `position_size` unchanged.** No I/O, no
  clock, no `Settings`.
- **Models** (`app/models/backtest.py`): frozen `BetCandidate` (entry decision, stake
  decided later), `ClosedBet`, `EquityPoint`, `StrategyBreakdown`, `BacktestResult`,
  `MonteCarloResult`, `MarketResolution` (the outcome input), and `BacktestParams` (the
  pure mirror of the `EDGE_*` knobs).
- **Replay adapter** (`app/backtest/engine.py`): `build_candidates` (walk quotes, rebuild a
  1-level book from top-of-book, run `extreme_correction` + `set_arb`, first entry per
  (market, strategy)), `run_backtest_once` (DB read → candidates → `simulate`), and a CLI
  `python -m app.backtest.engine <resolutions.json>` (boundary-validated outcomes).
- **Store** (`app/ingestion/store.py`): `load_quotes` — time-ordered reader with optional
  token filter and half-open `[start, end)` window (read counterpart to `insert_quotes`).
- **Config** (`app/config.py`): `EDGE_BACKTEST_INITIAL_BANKROLL`, `EDGE_KELLY_{FRAC,CAP}`,
  `EDGE_CORR_CAP_FRAC`, `EDGE_MODEL_ERROR_MARGIN`, `EDGE_MC_{SIGMA,SIMS,SEED}` (Decimal money).
- **Tests**: **+29** (21 pure backtest worked examples incl. a win/loss/overlap known-answer,
  risk-free arb, edge-gate rejection, the correlation-cap clamp, a no-look-ahead invariance
  check, the `resolve > entry` guard, and MC determinism/seed/known-answer; 6 offline
  `build_candidates`; +1 DB-gated `load_quotes`; +1 DB-gated end-to-end arb replay).

### Verified
- `cd quant && uv run pytest tests/test_backtest.py tests/test_backtest_engine.py -q` →
  **27 passed, 1 skipped** (the engine DB test skips without a DB).
- `cd quant && uv run pytest -q` → **176 passed, 11 skipped** (no regressions).
- With `EDGE_TEST_DATABASE_URL=…edge_test` → **187 passed** (incl. `load_quotes` ordering/
  window/Decimal guard and the seeded arb replayed through the store end-to-end to **1000.03**).
- **Live smoke** `uv run python -m app.backtest.engine /tmp/edge_resolutions.json` over the
  dev DB's stored quotes → replayed 5 markets, placed **2** `extreme_correction` bets (the
  deep longshots that cleared the cost gate), both lost → final **961.14**, return **−3.89%**,
  max drawdown 3.89% — costs baked in (most correction edges were gated out, as expected).

### Money-math / correctness decisions (carry forward)
- **No look-ahead** is structural: `simulate` merges entry + resolution events, processes
  them in time order with **resolutions before entries on ties** (free capital first), so
  entry sizing can only see capital freed by resolutions strictly *before* the entry. A
  candidate is built from one tick's quote and resolves only at `resolve_time`. Pinned by a
  test: flipping a later outcome leaves all earlier stakes unchanged.
- **Costs are in the result, not just the gate.** Directional fill price is all-in
  (`m_side + half_spread + slippage + gas`); a win pays $1/share, a loss pays $0. Arb P&L is
  the locked `net_edge*set_size` (already net of gas+slippage). Sizing still uses the existing
  `position_size` (gate on `p_lo`, Kelly on the ask `m+half_spread`), so realized return is
  ≥-conservative vs the gate.
- **Bankroll = available cash** (one unambiguous base for Kelly, the hard cap, and the
  correlation cap). Equity sampled at resolutions equals `initial + cumulative realized P&L`.
- **Correlation cap is streaming**: open exposure per `tag` (market `category`, else the
  condition id) may not exceed `corr_cap_frac * cash` — the streaming analogue of the batch
  `cap_correlated_stakes` (which needs all positions at once).
- **Directional probability source**: `extreme_correction.fair_value` is `p_yes`;
  `p_side = p_yes` (buy_yes) or `1 - p_yes` (buy_no); `p_lo_side = p_side - model_error_margin`
  (the CI lower bound). `favourite_longshot` has no probability → excluded from sizing.
- **Monte-Carlo**: each market's YES outcome ~ `Bernoulli(clip(p_yes + N(0, mc_sigma), 0, 1))`,
  then the full causal `simulate` re-runs (sizing adapts per path). The Gaussian is the only
  float and it only ever decides a 0/1 outcome — **the bankroll arithmetic stays exact
  Decimal**. `rng` is injected and seeded for determinism.

### Decisions locked this slice
- **One entry per (market, strategy)** — the first qualifying tick, held to resolution
  (re-entry / exit policies are future work).
- **Arb is taken whole or skipped** (no fractional sets): funded only when its set cost fits
  available cash and the per-tag allowance.
- **Top-of-book only** is all the stored depth, so `set_arb` is priced from a reconstructed
  1-level book (depth-aware arb pairs with a full-book history slice, deferred).
- **Outcomes are an explicit `resolutions` input** (no resolution-watcher this slice); the
  CLI reads a boundary-validated JSON file. `BacktestParams` knobs are the `EDGE_*` mirror.
- **Look-ahead review** (subagent, money-math + look-ahead only): no look-ahead leak found
  (signals never see the outcome; sizing never sees `resolutions`). Two gaps fixed —
  `BetCandidate` now rejects `resolve_time ≤ entry_time` (a degenerate candidate would have
  let `simulate`'s tie ordering drop the resolution and lock the stake), and `monte_carlo`
  seeds its rng from `params.mc_seed` when none is injected. Two findings declined as
  intentional & spec-mandated: Kelly sizes on `m+half_spread` with slippage+gas in the gate
  only (CLAUDE.md), and the bankroll base is available cash (the solvency-safe choice —
  equity-base could over-commit and drive cash negative).

## Slice: Web design system + app shell  ✅ done (2026-06-16)

First `web/` slice: scaffold the Next.js (App Router) advisor dashboard and build the
neon-glass design language + reusable app shell. **Design system + shell only** — no data
wiring (no BFF routes, Zod, openapi-typescript, SSE/Redis); cards render static placeholder
signals purely to exercise the primitives.

### What's done
- **Scaffold** (`web/`): Next 15 App Router, React 19, **strict TS** (`noUncheckedIndexedAccess`
  on), `sass` for SCSS Modules, `vitest` + RTL + jsdom. No Tailwind. **pnpm via corepack**
  (`pnpm@9.15.0`, pinned in `packageManager`) — the system pnpm 11 needs Node ≥22.13 and is
  unusable on this Node 20.12, so all web commands run as `corepack pnpm@9.15.0 …`.
- **Token system** (`src/styles/tokens.scss`): dark-purple neon palette, glass-surface tokens,
  and a runtime `--glow` intensity (1 in dark, 0.25 in light) — every neon box-shadow scales by
  it, so light reads as crisp glass and dark as neon. `_mixins.scss` (`glass`, `neon-glow`,
  `text-glow`, `glow-color()`) is auto-injected into every module via `next.config`
  `sassOptions.additionalData`.
- **Theme** (`src/lib/theme.ts` + `ThemeToggle`): `[data-theme]` on `<html>`, **defaults to
  prefers-color-scheme**, explicit choice persisted to localStorage; a tiny no-flash inline
  script in `<head>` sets the attribute before paint. Toggle is a real `role="switch"`.
- **Shell + primitives**: `AppShell` (sticky glass top bar — wordmark, nav, live pill, toggle —
  + centered max-width layout); `GlassCard` (strong/interactive/glow variants), `Badge` (gate
  variants pass/watch/gated + dot/pulse), `EdgeMeter` (linear neon meter with a gate-threshold
  tick; pure `edgeMeterModel` exported for tests). Demo `page.tsx` renders a ranked edge list
  from static placeholders. Fonts via `next/font` (Syne display / Sora body / JetBrains mono).
- **a11y**: `prefers-reduced-motion` disables the aurora drift + all transitions; meter exposes
  `role="meter"` + `aria-valuetext`; semantic landmarks (banner/nav/main/region).

### Verified
- `cd web && corepack pnpm@9.15.0 test` → **21 passed** (theme resolution + no-flash script;
  `edgeMeterModel` status/clamp geometry; primitive render + a11y roles; toggle flips
  `[data-theme]` and persists).
- `corepack pnpm@9.15.0 build` → compiles clean (strict TS + lint pass), 4 static routes.
- **Rendered + screenshotted** dark and light via Playwright: toggle works and defaults to the
  OS preference (Chromium's light default rendered light first, as designed). **WCAG**: measured
  contrast on glass-over-canvas — primary text ≥15:1, dim ≥8:1, faint labels ≥6:1 (AA pass) in
  both themes, after darkening the light-mode faint token (was 4.32:1, below AA).

## Slice: Advisor REST API + live Signals page  ✅ done (2026-06-16)

Connect the two halves: expose the quant money-math as a read API and wire the dashboard to
it. The quant layer had all the math (signals, Kelly sizing, calibration, backtest) but only
`GET /health`; the web layer had the design system but rendered hardcoded placeholders. This
slice adds the endpoints, a typed Zod-validated web client, a sortable **Signals** page, and a
**signal detail** view (sizing breakdown + cost gate). **REST + signals page only** — streaming
(SSE/Redis) stays phase 4.

### What's done
- **Pure enrichment** (`quant/app/advisor/view.py`): `advise()` joins a detected `Signal` with
  the live quote and **reuses `position_size`/`edge_gate` unchanged** to produce a recommended
  fractional-Kelly stake, the cost-gate breakdown, and a `[0,1]` confidence. The directional
  mapping mirrors `backtest._directional_candidate` **exactly** (side-token midpoint/spread,
  `p_lo = p_side − margin`) so the live advisor and the replay size identically. Frozen
  `AdvisedSignal`/`GateBreakdown` models (`app/models/advisor.py`).
- **Store readers** (`app/ingestion/store.py`): `load_signals` (newest-first, rebuilds the
  concrete `Signal` by `strategy`) and `load_latest_quotes` (`DISTINCT ON (token_id)`).
- **Routers** (`app/api/`, mounted in `main.py`): `GET /signals` (enriched, sorted by net edge,
  `?bankroll=`/`?strategy=`/`?limit=` knobs) + `GET /signals/{id}`; `GET /calibration`
  (`summarize`, `null` on an empty journal); `GET /backtest` (loads `EDGE_BACKTEST_RESOLUTIONS_PATH`
  if set, else a well-formed `n_bets=0` report). `Settings` gains `backtest_resolutions_path`.
- **Web client + boundary** (`web/src/lib/`): `zod` added; `schemas/signal.ts` + `schemas/report.ts`
  parse the **Decimal→JSON-string** money contract (coerced to number for display); typed
  `api/client.ts`; **BFF route handlers** (`app/api/signals`, `app/api/signals/[id]`) keep
  `QUANT_API_URL` server-only.
- **Signals page** (`web/src/app/signals/`): client component → `/api/signals` → a **sortable**
  table reusing `EdgeMeter`/`Badge`/`GlassCard` (market, strategy, price m, your p, edge meter,
  size, net-of-cost edge), rows deep-linking to **`/signals/[id]`** — a server component on
  `GET /signals/{id}` rendering the full Kelly→cap sizing breakdown and the cost gate
  component-by-component. `AppShell` nav now routes Signals → `/signals`.

### Verified
- `cd quant && uv run pytest -q` → **183 passed, 15 skipped** (offline; +7 `advise` worked-number
  unit tests: cap-binds $500, gated-out, **buy_no side-quote mapping**, arb, longshot, confidence
  bounds). With `EDGE_TEST_DATABASE_URL` → **198 passed** (+4 DB-gated ASGI integration tests:
  shape, net-edge sort, the **Decimal→string** money assertion, `/signals/{id}` gate breakdown +
  404, `/calibration` n, `/backtest` n_bets=0).
- `cd web && corepack pnpm@9.15.0 test` → **28 passed** (+7: Zod boundary parse + SignalsTable
  render/sort/empty). `build` clean (strict TS, 7 routes incl. the 2 BFF handlers).
- **Live smoke** (Playwright, dark + light): seeded the dev DB (one market, both tokens' quotes,
  one signal per strategy), ran `uvicorn` + `next dev`; `/signals` rendered all three rows ranked
  by net edge (directional $50.00 / +600 bps / Gate ✓, arb +300 bps / Gate ✓, longshot below
  gate), and `/signals/{id}` showed the gate `p_lo 0.50 > threshold 0.44 — gate passes`.

### Money-math / correctness decisions (carry forward)
- **Live == replay**: `advise` reuses `position_size` and the exact directional mapping, so the
  recommended stake equals what the backtest would size (pinned by the cap-binds + buy_no tests).
- **Decimal → JSON string** is the deliberate wire contract (no float in the money path); the web
  Zod boundary coerces to number for **display only**. Pinned by a test asserting the field is a str.
- **Side-token quote**: directional sizing uses the side you'd buy (yes-token for `buy_yes`,
  no-token for `buy_no`) — mis-mapping silently mis-sizes; guarded by the buy_no worked example.
- **`confidence`** is a signal-local heuristic: directional `clamp((p_lo−threshold)/(1−threshold),
  0,1)`, arb `1` (risk-free), longshot `edge_score`. Calibration will shrink it later.
- **Longshot has no money edge**: `edge_score` is a dimensionless [0,1] strength, so it feeds
  `confidence` only — `edge`/`net_edge` stay 0 so it never out-ranks an actionable signal.
- **No PK on `signals`** → ids are synthesized `strategy:market_id:epoch_ms` (round-trippable;
  same-ms collision negligible at the 15s scan cadence). A surrogate `id` column is the long-term
  fix (the table is *regular*, so a real PK is feasible) — see open questions.
- **`/backtest` degrades gracefully**: empty resolutions → valid `n_bets=0` report (no
  resolution-watcher yet); **`/calibration`** returns `null` on an empty journal (scoring zero
  records is undefined).

## Slice: Calibration + Backtest dashboard pages (the views that keep you honest)  ✅ done (2026-06-16)

The accountability UI: two dashboard pages that surface whether the advisor's probabilities are
real (Calibration) and how the strategy behaves over a full replay including the *distribution*
of outcomes, not just the median (Backtest). The math + endpoints existed; this slice builds the
views, with **hand-rolled SVG charts (no new dependency)** following the existing `EdgeMeter`
pattern, and extends the API with the two fields the pages needed. **Advisor only — read views.**

### What's done
- **Quant API extensions** (reuse already-tested math; no new money-math):
  - `calibration_timeline` (`app/math/calibration.py`) — cumulative Brier & log-loss per distinct
    journal timestamp (the final point equals the overall metrics); `CalibrationTimePoint` +
    `timeline` on `CalibrationSummary`. Wired into `summarize`.
  - `simulate_with_distribution` (`app/math/backtest.py`) — the deterministic replay **plus** the
    existing `monte_carlo` attached (`None` for a zero-bet replay); nullable `monte_carlo` on
    `BacktestResult`; `run_backtest_once` calls it via `run_in_threadpool` (MC re-runs `simulate`
    `mc_sims`× and must not block the event loop). Deterministic via `mc_seed`.
- **Charting** (`web/src/lib/charts.ts`, pure + tested): `linearScale`, `buildLinePath`,
  `buildAreaPath`, `niceTicks`, `plotArea`. No charting library added (SPEC.md never named one;
  CLAUDE.md requires approving deps — decided with the user to hand-roll).
- **Chart components** (`web/src/components/charts/`, each a pure `*Model()` geometry fn + a
  thin `"use client"` SVG): `ReliabilityChart` (claimed-vs-realized + y=x diagonal, dots ∝ count),
  `MetricTimeline` (cumulative Brier & log-loss), `EquityCurve` (area + peak→trough drawdown band),
  `MonteCarloChart` (p5–p95 whisker / p25–p75 box / median / mean / start reference). Neon tokens;
  glows scale with `--glow` so light mode dims via the same CSS.
- **Pages** (`web/src/app/{calibration,backtest}/`): server components fetch + Zod-validate via the
  existing `getCalibration`/`getBacktest` (no new BFF route — mirrors the signal *detail* page),
  delegating to presentational `CalibrationView`/`BacktestView` (render from props → unit-testable).
  Calibration leads with the **Kelly-shrink hero** (adjusted fraction, overconfidence badge,
  diagnostics), then KPIs, both charts, per-strategy table; null-journal empty state. Backtest:
  KPIs, equity curve, the Monte-Carlo distribution, per-strategy table; zero-bet explainer state.
  `report.ts` Zod gains the `timeline` + `monte_carlo` shapes; `AppShell` nav links activated.
- **Demo seed** (`quant/app/seed_demo.py`, dev only): builds an overconfident calibration journal
  + extreme/arb markets whose quotes replay into real bets; modes `--dry-run`, `--serve-mock`
  (serves the seeded reports from the real quant math, no DB), and a DB seed + resolutions JSON.

### Verified
- `cd quant && uv run pytest -q` → **189 passed, 15 skipped** (DB-gated); +4 calibration-timeline
  worked examples, +2 `simulate_with_distribution` (deterministic MC present / `None` for no bets),
  + MC & timeline assertions added to the DB-gated engine + API tests.
- `cd web && corepack pnpm@9.15.0 test` → **61 passed** (+33: schema coercion incl. timeline +
  monte_carlo, chart geometry incl. exact coordinates, view renders incl. empty / insufficient /
  zero-bet states). `tsc --noEmit` strict clean; `next build` green (9 routes; `/calibration` +
  `/backtest` dynamic, server-rendered).
- **Seed dry-run** → calibration 35 records (Kelly shrinks 0.25→**0.224**, 6 timeline points, 3
  strategies); backtest 5 bets, **+63.8%**, max DD 1.1%, MC over 1000 sims spanning $942→$1418,
  P(loss) 59% (the longshot character — a real spread, not a point mass).
- **Screenshots** (isolated headless Chrome, dark/canonical theme; the data served from the real
  quant math via `seed_demo --serve-mock`): both pages match the neon-glass design system — glass
  cards, neon palette (cyan equity / violet MC box / red drawdown band), mono numerals, the Kelly
  hero, the reliability dots below the diagonal, and the Monte-Carlo box-and-whisker. (Light reuses
  the same token-driven components; the MCP browser profile was locked, so headless was used.)

### Money-math / correctness decisions (carry forward)
- **No new money-math**: the timeline reuses `brier_score`/`log_loss` applied cumulatively (worked-
  example tested before use); the MC path reuses `monte_carlo` unchanged (its only float still just
  decides a 0/1 outcome — bankroll stays exact Decimal). Decimal→JSON-string contract unchanged.
- **Kelly adjustment is display-only** here — surfaced prominently, but **not** fed into the live
  sizing knobs (that stays a future wiring item).
- Charts derive geometry in pure `*Model()` fns (unit-tested with exact coordinates), separate from
  rendering — same discipline as `edgeMeterModel`.

### Decisions locked this slice
- **Hand-rolled SVG, zero new deps** (vs Recharts/visx) — SPEC.md named no charting lib and both
  SPEC §9 + CLAUDE.md gate new deps; the `EdgeMeter` precedent makes SVG the on-pattern choice.
- **Server-component pages + client chart children**; no new BFF route (the typed client is called
  directly, like the signal detail page).
- `seed_demo.py` is **dev tooling, not a migration**; it runs the backtest with demo-friendly knobs
  (margin 0.02, zero slippage/gas) so the extreme-correction longshots clear the gate — the default
  costs would gate them out, leaving a degenerate (arb-only) MC.

## Slice: Live arb updates — CLOB WebSocket → Redis pub/sub → SSE  ✅ done (2026-06-16)

Closed the streaming loop for the **arbitrage path only**: subscribe to the Polymarket CLOB
market WebSocket, keep an in-memory order book updated by deltas, re-run the **existing** set-arb
math on every book change, enrich via the **existing** `advise`, publish high-net-edge signals to
a Redis channel, and stream them to the dashboard over SSE — conflated server-side so the client
re-renders ~10/s, not at raw WS rate. **Advisor only — detect + surface; never executes.**
**Stream-only**: live detections are NOT written to `signals` (the periodic scan engine stays the
source of persisted rows). No Kafka (Redis is enough at this scale).

### What's done
- **Untrusted WS boundary** (`quant/app/polymarket/schemas.py`): `RawWsBook` / `RawWsPriceChange` /
  `RawWsChange` (prices/sizes as `str`, `extra="ignore"`) + `parse_ws_message` (dispatch on
  `event_type`; ignore `tick_size_change`/`last_trade_price`/unknown).
- **In-memory book, pure** (`app/streaming/book_state.py`): `LiveBook` (per-token `price→size`
  maps; `apply_book` replaces, `apply_price_change` upserts with **size 0 = remove**;
  `snapshot()` → canonical frozen `OrderBook`). `BookStore` (token→market index;
  `apply(frame)` → affected `market_id`; `market_books()` → both legs once seen). **Decimal from
  the wire string, never float** (mirrors `transform.orderbook_from_raw`).
- **Redis publisher** (`app/streaming/redis_bus.py`): `publish_signal` → `AdvisedSignal.model_dump_json()`
  (Pydantic renders `Decimal` as a JSON **string** — same wire contract as REST).
- **Stream engine** (`app/streaming/engine.py`): `run_stream` is the timing-free, injectable seam —
  consume frames → keep books live → `evaluate_market` on the affected market → dedup by `net_edge`
  → `advise(signal, signal_id="set_arb:<market_id>", …)` → publish. Per-frame isolation (one bad
  frame never kills the loop). `connect_clob_ws` (real `websockets` source, reconnect/backoff) +
  CLI `python -m app.streaming.engine` (live) / `--mock` (synthetic dev feed, mirrors
  `seed_demo --serve-mock`).
- **Config** (`app/config.py`): `EDGE_REDIS_URL`, `EDGE_SIGNALS_CHANNEL`, `EDGE_CLOB_WS_URL`.
  Deps: `websockets`, `redis` (quant); `ioredis` (web).
- **Web SSE** (`web/src/app/api/stream/route.ts`, Node runtime): subscribe to Redis via ioredis,
  **conflate** with `Conflator` (last-write-wins per id, 100ms flush → ≤10 frames/s), Zod-validate
  each inbound message, emit `text/event-stream` with a 15s heartbeat; teardown on cancel/abort.
- **Signals page** (`web/src/app/signals/page.tsx`): after the initial REST load, open an
  `EventSource("/api/stream")` and `mergeSignal` each update in place (replace-by-id, else prepend;
  `src/lib/stream.ts`, pure + tested). Badge flips **"streaming · live"** when connected, falls
  back to "REST · reconnecting…" on error. `SignalsTable` unchanged (still sorts client-side).

### Verified
- `cd quant && uv run pytest -q` → **208 passed, 15 skipped** (was 189 offline; +19: book
  add/update/remove + Decimal exactness, the mock-WS-feed worked example **net 0.03** = YES 0.46 +
  NO 0.49, dedup/no-republish, malformed-frame isolation, WS-schema parse + the Decimal→string
  wire-contract assertion). No regressions.
- `cd web && corepack pnpm@9.15.0 test` → **66 passed** (+5: `mergeSignal` replace/prepend/no-mutate,
  `Conflator` last-write-wins + flush-clears). `build` clean (strict TS; `/api/stream` dynamic route).
- **Live manual check** (the task's explicit ask): `docker compose … up -d` (Redis), seeded the dev
  DB, ran `uvicorn` + `python -m app.streaming.engine --mock` + `next dev`. `curl -N /api/stream`
  streamed `: connected` + conflated `data:` frames. In Chrome (Playwright), `/signals` showed the
  badge **"streaming · live"** and the three `mock-1/2/3` rows — which were **never in the REST
  payload** — appearing live, interleaved with the seeded signals and ranked by net edge
  (`live-signals-streaming.png`).

### Money-math / correctness decisions (carry forward)
- **Live == replay**: the stream reuses `evaluate_market` + `advise` unchanged, so a live arb edge
  equals what the periodic scan and the backtest compute. Conflation/dedup only **drop intermediate
  frames** — they never alter a value. **Decimal straight from the wire string**, no float.
- **Stable id `set_arb:<market_id>`** (not the scan's `strategy:market_id:epoch_ms`) so the SSE
  conflator and the client merge **replace the row in place** rather than appending forever.
- **Dedup by `net_edge`**: book deltas re-fire constantly; publish only when the net edge moved.
- **Stream-only** (no DB write at WS rates) — the `signals` table has no PK/dedup; the periodic
  scan remains the persistence path.

### Decisions locked this slice
- **Redis pub/sub, not Kafka** (scope). The web SSE endpoint owns conflation (server-side ≤10/s),
  keeping the client dumb. **Arb path only** — directional/price signals still flow via REST.
- An edge that *disappears* leaves its last row on the dashboard (no "removal" event yet) — a
  staleness/TTL policy is future work (see open questions). The live engine is a **standalone CLI**
  (mirrors `signals/engine.py`), not a FastAPI lifespan task.

## Slice: Observability & alerting — "know when something breaks"  ✅ done (2026-06-16)

The operational-visibility layer. Until now a moving part could stop silently (a dropped CLOB
WS, a strategy bleeding drawdown, the model drifting overconfident) — exactly the failure that
loses money quietly. This slice adds **structured JSON logging** across both services, **Sentry**
error capture (quant), **basic Prometheus metrics** (latency / poller health / signal counts),
and **three application-level alerts** (dropped WS, drawdown breach, calibration drift) surfaced
as **dashboard toasts** alongside high-net-edge opportunity notifications. Alert *decisions* are
pure, tested predicates that **reuse the existing `max_drawdown` + calibration math** — no new
money-math. **App-level alerting** (Redis `edge:alerts` → SSE → toast + Sentry); deliberately **no
Prometheus/Grafana/Alertmanager containers** (keep infra minimal — `/metrics` is exposed for an
optional future scraper).

### What's done
- **Structured logging** (`quant/app/observability/logging.py`): `JsonFormatter` (one JSON object
  per line — `ts/level/logger/msg/service` + `extra=` fields + rendered `exc`); `configure_logging`
  replaces the per-CLI `basicConfig` in the FastAPI lifespan + all four CLIs + the new monitor.
  `EDGE_LOG_LEVEL`/`EDGE_LOG_JSON` knobs. Web mirror `web/src/lib/logger.ts` (same five base fields).
- **Sentry** (`quant/app/observability/sentry.py`): `init_sentry(service)` — no-op without
  `EDGE_SENTRY_DSN`; `LoggingIntegration(event_level=ERROR)` turns every `logger.exception` in the
  loops into an event for free. `capture_alert` surfaces the three alerts as events. **No web Sentry**
  (`@sentry/nextjs` deferred — web errors go to the structured logger).
- **Prometheus** (`quant/app/observability/metrics.py`): families `edge_http_request_duration_seconds`,
  `edge_poller_scans_total` / `edge_poller_scan_duration_seconds` / `edge_poller_last_success_timestamp_seconds`
  / `edge_poller_quotes_written_total`, `edge_signals_total{strategy,source}`,
  `edge_ws_connects_total`/`edge_ws_drops_total`/`edge_ws_up`, `edge_alerts_total{kind,severity}`.
  FastAPI serves `GET /metrics`; each CLI exposes its own via `start_metrics_server(EDGE_METRICS_PORT)`
  (best-effort; a bind clash is logged, never fatal). Increments live in the I/O/loop layer only —
  pure math + the `run_*_once`/`run_stream` seams are untouched, so prior tests stayed green.
- **Alerts core** (`quant/app/models/alert.py` + `quant/app/observability/alerts.py`): frozen
  Decimal-native `Alert` (Decimal→JSON-string wire contract). Pure predicates `evaluate_ws_drop`,
  `evaluate_drawdown` (reads `BacktestResult.max_drawdown`), `evaluate_calibration_drift` (reads
  `KellyAdjustment.claimed_avg − realized_avg`, the overconfidence gap that shrinks Kelly). New
  `EDGE_*` threshold/channel knobs.
- **Alert bus + WS-drop wiring** (`quant/app/observability/alert_bus.py`): `publish_alert` counts
  `edge_alerts_total`, captures to Sentry, publishes JSON to `edge:alerts`. `connect_clob_ws` gained
  an injected `on_drop` seam (+ `connect`/`sleep`/`max_reconnects` for tests); `run_stream_forever`
  publishes a `ws_drop` alert on every reconnect. Dev `--mock-drop` forces the path with no socket.
- **Monitor loop** (`quant/app/monitoring/engine.py`): dedicated CLI (`python -m app.monitoring.engine
  [loop|once]`) evaluating drawdown (via `run_backtest_once`) + calibration drift (via
  `load_calibration` → `summarize`) on a cadence; `run_monitor_once` is the injectable seam (async
  fetchers in, alerts returned).
- **Dashboard** (web): `AlertSchema` (Zod, Decimal-string→number|null), `/api/alerts/stream` SSE
  (mirrors the signals route, no conflation), `NotificationsProvider` + `Toast` (GlassCard tinted by
  severity → existing neon tokens; GlassCard glow extended with amber/red; each toast its own ARIA
  live region) mounted in `layout`. **One toast lane** for both system alerts and high-net-edge
  opportunity toasts; the latter fire via pure `shouldToastSignal` (rising edge across
  `HIGH_EDGE_THRESHOLD = 0.05`) on the existing signals stream.

### Verified
- `cd quant && uv run pytest -q` → **230 passed, 15 skipped** (was 208; +22 across logging/sentry/
  metrics/alerts/alert-bus/ws-drop/monitor). `cd web && corepack pnpm@9.15.0 test` → **79 passed**
  (+13: alert-schema, toast, notifications); `tsc --noEmit` clean; `next build` green (10 routes incl.
  `/api/alerts/stream`).
- **Metrics endpoint (live):** `curl localhost:8000/metrics` returned every `edge_*` family.
- **Forced-error → alert (dev, the task's explicit ask):** with `docker compose up` (Redis) and a
  `redis-cli SUBSCRIBE edge:alerts`:
  - `python -m app.streaming.engine --mock-drop` → `ws_drop` alerts on `edge:alerts`
    (`{"kind":"ws_drop","value":"1",...}`).
  - `seed_demo` (35-row overconfident journal + resolutions) then `python -m app.monitoring.engine once`
    (demo backtest knobs, thresholds dd 0.01 / drift 0.05) → **`drawdown_breach`** (max drawdown
    0.0114 ≥ 0.01) **and** `calibration_drift` (claimed 0.808 vs realized 0.730, gap 0.0784 ≥ 0.05),
    both on `edge:alerts`.
  - `next dev` + `curl -N /api/alerts/stream` while publishing → the SSE route forwarded the alerts as
    `data:` frames (Zod-coerced `value`/`threshold` to numbers) — the dashboard toast path end-to-end.

### Money-math / correctness decisions (carry forward)
- **No new money-math:** alert predicates reuse `max_drawdown` and the calibration `summarize`/
  `KellyAdjustment` unchanged. Drift = high-confidence `claimed_avg − realized_avg` (overconfidence);
  `None` when there's no high-confidence evidence. Decimal→JSON-string wire contract preserved for
  `Alert` (web Zod coerces to number for display only).
- **Metrics are side-effects in the I/O layer**, never inside pure math or the test seams.
- **Data gap (acknowledged):** no live equity feed / resolution-watcher yet, so drawdown runs off the
  **backtest replay** against `EDGE_BACKTEST_RESOLUTIONS_PATH` and drift off the **calibration journal**.

### Decisions locked this slice
- **App-level alerting**, not an Alertmanager/Grafana stack (CLAUDE.md "no infra beyond the task").
- **Deps (quant only):** `prometheus-client` + `sentry-sdk`. Structured logging is zero-dep (custom
  JSON formatter / `console.log`). Web Sentry deferred.
- **Dedicated monitor loop** for the two periodic alerts (drawdown/drift); WS-drop is event-driven in
  the streaming engine. **One dashboard toast lane** for alerts + opportunities.

### Re-verified (2026-06-17)
Re-ran the full verification today (no source changes since the slice was committed — nothing in the
tree to re-implement; this confirms it still works end-to-end):
- `cd quant && uv run pytest -q` → **230 passed, 15 skipped** (DB-gated). `cd web && corepack
  pnpm@9.15.0 test` → **79 passed**.
- **`/metrics` (live):** `uvicorn app.main:app` + `curl localhost:8000/metrics` rendered every
  `edge_*` family (`edge_http_request_duration_seconds`, `edge_poller_*`, `edge_signals_total`,
  `edge_ws_*`, `edge_alerts_total`); `GET /health` → `{"status":"ok"}`.
- **Forced-error → alert (dev):** `docker compose … up -d` (Redis), subscribed `edge:alerts` via the
  redis container, then:
  - `python -m app.streaming.engine --mock-drop` → `{"kind":"ws_drop",...}` published every reconnect.
  - `seed_demo` (35-row overconfident journal + `demo_resolutions.json`) then `python -m
    app.monitoring.engine once` with demo backtest knobs + thresholds dd `0.01` / drift `0.05` →
    **`drawdown_breach`** (max drawdown `0.01144…` ≥ `0.01`, severity error) **and**
    **`calibration_drift`** (claimed `0.807` vs realized `0.727`, gap `0.08` ≥ `0.05`), both on
    `edge:alerts`. (The web SSE → toast leg is covered by the web suite's alert-schema/SSE/toast tests.)
- **Tidied:** added `.playwright-mcp/`, `*.png` (verification screenshots), and the generated
  `quant/demo_resolutions.json` to `.gitignore` — none are source.

## Slice: Execution module — security architecture + pure core (Phases 1–3)  ✅ done (2026-06-17)

First slice of the long-deferred **execution module**: an ISOLATED service that will (in later,
separately-gated phases) place trades on Polymarket/Polygon. **Security architecture & threat
model came first** (plan approved before any code; a Plan agent designed the architecture and an
independent security-reviewer agent built the STRIDE/attack-tree threat model). This slice is the
**pure core only — ZERO keys, ZERO network, ZERO chain**: the intent model + canonical hashing,
the circuit-breaker predicates, the read-side advisor contract, and the persistence/audit schema.
No signer, no relay, no KMS, no signing code. Decisions locked with the user: **AWS KMS**
(secp256k1) for the future signer; **Gnosis Safe (M-of-N)** cold custody + small hot float;
pure-core-first build sequence. Full plan: `~/.claude/plans/read-claude-md-progress-md-scalable-meteor.md`.

### What's done (new top-level `executor/` service — sibling of `quant/`, never imports it)
- **Isolation by construction** (`executor/`): own `uv` project, own package, own Alembic chain,
  own database (`edge_exec`, separate from the advisor's `edge`). **Zero `import quant`** — it
  consumes advisor output as JSON over Redis, validated by its own boundary model (like `web/`'s
  Zod). `EDGE_EXEC_ENABLED` defaults **false** (the hard CLAUDE.md gate).
- **Intent model + tamper-evident hashing** (`app/models/intent.py`): frozen, `Decimal`-native
  `Intent` (the only thing the future signer signs) + `IntentEnvelope`. `compute_intent_hash` =
  SHA-256 of canonical `json.dumps(model_dump(mode="json"), sort_keys=True)` — float-free and
  order-independent, so executor and signer hash byte-for-byte. `IntentEnvelope.seal/verify` is
  the binding the signer re-checks to reject a tampered intent at the trust boundary.
- **Pure intent forming** (`app/orchestrator/intents.py`): `intent_from_signal` maps a
  **directional** (`extreme_correction` buy_yes/buy_no) `AdvisedSignalView` to a `clob_order`
  Intent — `ask = m + half_spread`, `size = notional/ask`, `max_price = min(1, ask + slippage)`.
  **Set-arb is deliberately NOT formed** here (see money-math note). No I/O, no clock (ids/nonce/
  times injected).
- **Circuit breakers** (`app/breakers/checks.py`): pure predicates over `(intent, state, limits)`
  — master switch, per-trade cap, slippage cap, contract/spender allowlist, rate limit (count
  **and** cumulative notional), hot-wallet cap (bounded by both the cap and available balance).
  `evaluate` aggregates all breaches. State is read from durable storage, never executor memory.
- **Read-side advisor contract** (`app/models/advised.py`): `AdvisedSignalView` mirrors
  `AdvisedSignal` (money as JSON strings → `Decimal`), `extra="ignore"` for additive drift.
- **Config** (`app/config.py`): `EDGE_EXEC_*` `Settings` (mirrors `quant/app/config.py`), Decimal
  money knobs, csv allowlist props, `breaker_limits()` projection. No secrets (future KMS handle is
  a non-secret id).
- **Persistence** (`app/db/tables.py` + `store.py` + Alembic `0001_exec_init`): `exec_intents`
  (append-only), `exec_audit` (insert-only spine), `exec_approvals` (stores only the token **hash**),
  `exec_nonces` (atomic `FOR UPDATE` allocator), `exec_allowlist`, `exec_breaker_counters`. All
  money unbounded `NUMERIC`↔`Decimal`. **Nothing stores keys/seeds/raw tokens/KMS creds.**

### Verified
- `cd executor && uv run pytest -q` → **32 passed, 5 skipped** (offline; store tests skip without a DB):
  intent hash determinism + JSON round-trip + tamper detection + frozen; directional mapping
  worked example (m 0.20 / half_spread 0.05 → ask 0.25, notional 100 → size 400, max_price 0.30;
  max_price clamps to 1); arb/no-gate rejected; every breaker boundary (caps inclusive, rate
  count+cumulative, allowlist, hot-cap vs balance, multi-failure aggregate); config default-off +
  allowlist parse; golden-JSON contract parse + extra-field tolerance.
- With `EDGE_EXEC_TEST_DATABASE_URL=…edge_exec_test` → **37 passed** (+7 DB-gated store: intent
  Decimal↔NUMERIC round-trip, append-only ordered audit trail, **monotonic/unique nonce allocator**,
  allowlist round-trip, approval stores only the token hash). **Mutation-tested** the nonce
  allocator (broke the increment → the test went red → reverted) to prove the DB tests have teeth.
- `EDGE_EXEC_DATABASE_URL=…edge_exec uv run alembic upgrade head` → all six `exec_*` tables created;
  `downgrade base` → `upgrade head` clean round-trip.
- **Isolation:** **zero `import quant`** from `executor/` (only docstring mentions); secret grep
  finds no private-key/seed/signing code (only the schema docstring asserting none are stored). The
  advisor suite is untouched and green (`cd quant && … pytest -q` → **245 passed** with the test DB).

### Money-math / correctness decisions (carry forward)
- **Intent hash is float-free & deterministic**: canonical serialization renders every `Decimal`
  as its exact string and `sort_keys` removes field-order dependence — so the (future) signer
  recomputes an identical hash from the received JSON. The Decimal→JSON-string wire contract is
  load-bearing here, not cosmetic. (Numerically-equal-but-differently-written decimals are NOT
  collapsed — round-trip stability through JSON is the property that matters; we deliberately do
  not `normalize()` to avoid exponent-format surprises.)
- **Set-arb intent-forming is deferred** (and the test pins it raises): `advise()` collapses the two
  arb legs into one `market_price` (set cost) and drops the per-leg VWAPs, so a correct, MEV-safe
  two-leg priced order can't be rebuilt from the live stream. Arb execution pairs with the on-chain
  CTF Split/Merge/Redeem legs + the relay (a later phase that consumes the richer signal).
- **Breakers are inclusive at the boundary** (`<=`); rate limiting covers **cumulative notional**
  (not just count) to defeat the split-into-many-small-trades bypass; a single trade is bounded by
  **both** the hot-float ceiling and the available balance.

### Decisions locked this slice
- **`executor/` is a separate service, not a `quant/` subpackage** — CLAUDE.md independence; the
  key-free advisor can never reach the signer/wallet by construction (separate process/deploy/IAM).
- **Signer enforces its OWN policy later** (the central threat-model finding: assume the executor
  host is compromised — the signer must default-deny, re-validate the intent hash, and hold the
  recipient/contract/method allowlist + caps independently). NONE of that is built this slice; it's
  Phase 4 (signer vs Polygon Amoy testnet via AWS KMS) — re-planned/approved before any signing code.
- **Breaker state is durable + signer-owned** (DB, not executor memory) so a restart can't reset
  the rate-limit counters (a classic bypass).
- No new deps (reuses pydantic/pydantic-settings/sqlalchemy/asyncpg/alembic — the advisor's stack).
- Dev/test dbs `edge_exec` / `edge_exec_test` created in the existing compose Postgres.

### Open questions / next phases (NOT this slice — each re-planned + approved first)
- **Phase 4 — signer service (AWS KMS, Polygon Amoy testnet):** EIP-712 + EIP-1559 digest build,
  KMS `(r,s,v)` reconstruction (round-trip recovered address in tests), default-deny policy +
  allowlist + caps + approval-token re-verification. No mainnet key.
- **Phase 0 research before mainnet:** confirm a real Polygon private-inclusion relay + its trust
  model + a tighter-slippage public fallback; pin exact current Polymarket contract addresses; the
  Gnosis Safe transaction-guard for on-chain allowlisting.
- **Phase 5–7:** relay client (dry-run on testnet) → approval workflow + web UI (single-use TTL
  token bound to the intent hash, decoded-intent rendering) → testnet E2E → capped mainnet canary
  with the Safe cold split live, behind `EDGE_EXEC_ENABLED=true` and explicit human sign-off.
- **Carry-forwards:** nonce recovery path for stuck/gapped txs (Phase 4); exact-allowance vs arb
  speed (bounded per-cycle approve, never infinite); CLOB orders bypass the relay (off-chain
  EIP-712) so MEV protection covers only the CTF on-chain legs.

## Slice: Trades ingestion (Data API /trades → trades hypertable)  ✅ done (2026-06-17)

Roadmap Трек A1 (фундамент для fair-value + реального P&L): типизированный клиент Data API
`/trades`, чистый raw→canonical transform, `trades` hypertable + миграция, store-функции и
standalone-поллер. **Ingestion only** — никаких сигналов/денежной математики поверх; trade prints
это reference-данные (компаньон к `quotes`).

### What's done
- **Untrusted boundary** (`app/polymarket/schemas.py`): `RawTrade` (`asset`/`conditionId`/`side`/
  `size`/`price`/`timestamp`/`transactionHash`, `extra="ignore"`). **Деньги приходят JSON-числами**
  (напр. `price:0.8099999954639995`), поэтому клиент декодит ответ с `parse_float=str` — точный
  wire-литерал сохраняется строкой, **float не входит в денежный путь** (расширение правила CLOB).
- **HTTP** (`app/polymarket/http.py`): `request_json` получил опц. `parse_float` (форвардится в
  `json.loads`); дефолтный быстрый путь `response.json()` не тронут.
- **Data API клиент** (`app/polymarket/data_client.py`): `DataClient.get_trades(condition_id, limit)`
  — gotcha: query-параметр зовётся `market`, но берёт **condition id**.
- **Pure transform** (`app/ingestion/trades_transform.py`): `trade_from_raw` — единственная точка
  коэрции trade-print строк в `Decimal` (из строки, не из float), unix→UTC datetime.
- **Canonical model** (`app/models/trade.py`): frozen `Decimal`-native `Trade`
  (time/token_id/market_id/price/size/taker_side/trade_id).
- **Schema**: `trades` hypertable (`app/db/tables.py` + Alembic `0005_trades`, по образцу `quotes`;
  unbounded NUMERIC; индекс `(token_id, time desc)`; без PK — ограничение TimescaleDB).
- **Store** (`app/ingestion/store.py`): `insert_trades` (batch) + `load_trades` (time-ordered,
  опц. token-фильтр и half-open `[start,end)` окно) — по образцу quotes.
- **Поллер** (`app/ingestion/trades_engine.py`): `run_trades_scan_once` (timing-free seam) грузит
  tracked-вселенную, фетчит трейды per condition_id, маппит каждый print на рынок (чужие токены
  отбрасываются), батч-инсертит; per-market изоляция. Дедуп между циклами через инжектируемый
  `since`-курсор (token→last seen time); `run_trades_poller` + CLI
  `python -m app.ingestion.trades_engine [once|loop]`. Кноб `EDGE_TRADES_LIMIT`, `EDGE_DATA_BASE_URL`.

### Verified
- `cd quant && uv run pytest -q` → **255 passed** (было 245; **+10**: 3 transform worked-example
  incl. exact-Decimal/не-float-mediated, 2 data-client request/parse, 2 DB-gated store
  round-trip/фильтр/окно, 3 engine orchestration: маппинг/чужие токены/per-market изоляция/`since`-дедуп).
- `uv run alembic upgrade head` → `0004_calibration → 0005_trades`; `trades` hypertable подтверждён
  через `timescaledb_information.hypertables`.
- **Live smoke** `python -m app.ingestion.trades_engine once` (dev DB + реальный Data API) → 11
  tracked-рынков, **500 трейдов** записано; в БД — точные Decimal-цены (0.08 / 0.92, без float-
  артефактов), 10 distinct токенов.

### Money-math / correctness decisions (carry forward)
- **Trade price/size приходят JSON-числами** (не строками, как у CLOB) — декодим с `parse_float=str`,
  храним точный литерал, коэрсим в `Decimal` один раз в transform. Float не касается денег.
- **Дедуп — carry-forward**: `since`-курсор снимает повтор между циклами в рамках процесса; restart-
  durable high-water + same-second дедуп отложены (`trade_id` = tx hash, не уникален на fill; таблица
  append-only reference). Под нагрузкой одной секунды граничный трейд может продублироваться.
- `market_id` резолвится поллером (мы фетчим per condition_id, так что рынок известен); чужие токены
  в ответе отбрасываются защитно.

## Slice: Category resolution (Gamma event tags → fee category)  ✅ done (2026-06-17)

Roadmap Трек A2: Gamma `/markets` почти всегда без `category` (наблюдалось NULL), а fee-таблица
(A3) на ней keyится. Резолвим категорию из тегов события. **Находка**: `/markets` НЕ отдаёт
`events[].tags` (проверено 3 параметра), теги есть только в `/events` — поэтому категория
доводится вторичным батч-запросом `/events?id=...` по выбранной вселенной.

### What's done
- **Boundary** (`app/polymarket/schemas.py`): `RawGammaTag` (id/label/slug, `extra="ignore"`);
  `RawGammaEventRef.tags: list[RawGammaTag] = []` (пусто на пути `/markets`).
- **Pure logic** (`app/ingestion/transform.py`): `TAG_CATEGORY` словарь slug/label→каноническая
  категория (crypto/politics/sports/finance/economics/geopolitical; топиковые теги маппятся,
  generic — `all`/`pop-culture`/`exchange` — дают `None`); `category_from_tags` (первый
  смапленный тег по порядку); `derive_category` (явная `category` нормализованная побеждает, иначе
  из тегов событий, иначе `None`). `market_from_raw` теперь зовёт `derive_category`.
- **Client** (`app/polymarket/gamma_client.py`): `fetch_event_tags(event_ids)` → `event_id→tags`
  через батч `/events?id=...&id=...` (dedupe ids; малформед-событие пропускается, не фатально).
- **Discovery enrichment** (`app/ingestion/scanner.py`): `_resolve_categories` — для выбранных
  рынков без категории один батч-фетч тегов и `model_copy(category=…)`; решение чистое
  (`category_from_tags`), фетч best-effort (сбой → дефолты, не падаем).

### Verified
- `cd quant && uv run pytest -q` → **267 passed** (было 255; **+12**: 8 pure category worked-
  examples — топик/generic/first-wins/label-fallback/explicit-wins/derive/none + market_from_raw
  интеграция; 2 client request/parse incl. repeated `id` param; 3 enrichment — только
  некатегоризованные, без фетча когда не нужно, fallback при сбое). Без регрессий.
- **Live smoke** `discover_universe` (EDGE_TOP_N=8, реальный Gamma) → 8 рынков, **все
  категоризованы** (sports×3 / politics×3 / geopolitical / finance), **ноль `None`** — раньше все
  были бы `None`. Резолв из реальных `/events` тегов работает end-to-end.

### Decisions locked this slice
- **Теги — из `/events`, не `/markets`** (ограничение Gamma): категория доводится вторичным
  батч-запросом по уже выбранной (~top_n) вселенной — дёшево (один вызов на discovery-цикл), не
  N+1. Generic-теги → `None` → fee-таблица применит самый консервативный дефолт (корректно).
- Словарь `TAG_CATEGORY` расширяемый; неизвестный тег безопасно даёт `None`.

## Slice: Per-category taker fee table (math/fees.py)  ✅ done (2026-06-17)

Roadmap Трек A3 (SPEC §6): чистая денежная математика комиссии по категории — вход для
будущего fair-value gate. Пара к A2 (category → fee). **Math + tests only** — ещё не вшито в
gate/sizing (как и прочая math-модуль перед своим scanner'ом; сейчас gate использует
gas+slippage кнобы).

### What's done
- **Pure math** (`app/math/fees.py`, Decimal-native): `phi(price, category)` =
  `feeRate·(p(1−p))^exp` (per-dollar ставка, пик при p=0.5, ноль на краях); `fee_per_share =
  price·phi`. `FeeParams` + `FEE_TABLE` (crypto/politics/finance/sports/economics/geopolitical);
  `params_for` case-insensitive, **unknown/None → консервативный crypto-дефолт** (никогда не
  недооцениваем стоимость). Единственный нерациональный шаг — `exp=0.5` sqrt для economics —
  в фикс. Decimal-контексте (prec=50), без float.
- Ставки сверены с опубликованными peak-rate каждой категории.

### Verified
- `cd quant && uv run pytest tests/test_fees.py -q` → **13 passed** (peak φ(0.5) по таблице:
  crypto 1.8% / politics 1.0% / finance 1.0% / sports 0.75% / economics 1.5% / geopolitical 0;
  unknown==crypto; case-insensitive; φ пикует на 0.5 и симметрична; worked fee_per_share
  0.5→0.009 / 0.4→0.006912 / economics 0.5→0.0075; края→0; out-of-range price → ValueError).
- `cd quant && uv run pytest -q` → **280 passed** (было 267; +13; без регрессий).

### Decisions locked this slice
- **unknown/None → crypto rate** (самый консервативный) — uncategorized рынок не недооценивается.
- Поддержаны только опубликованные экспоненты `1` и `0.5` (иначе ValueError); таблица — единый
  источник, расширяется при изменении расписания комиссий Polymarket.

## Slice: Fair-value model + CI lower bound (math/fair_value.py)  ✅ done (2026-06-17)

Roadmap Трек A4: дать направленным сигналам реальную `p` + консервативную `p_lo` (вход в готовые
`edge_gate`/`position_size`). Выбран подход (с пользователем): **TWAP midpoint + дисперсионная
нижняя граница**. **Math only** — ещё не вшито в живой directional-scanner/sizing (это A6 /
sizing-wiring); конкретная модель калибруется позже по A5.

### What's done
- **Pure math** (`app/math/fair_value.py`, Decimal-native): `estimate_fair_value(observations,
  *, as_of, params)` → `FairValueEstimate{p_hat, p_lo, sigma, n}` или `None` при нехватке данных.
  `p_hat` = dwell-time-weighted mean(midpoint) (каждый midpoint «держится» до следующего тика,
  последний — до `as_of`); `sigma` = time-weighted stdev; `p_lo = clamp(p_hat − k·sigma, 0, 1)`.
  `FairValueObservation{time, midpoint}` (декаплинг от quotes), `FairValueParams{k=2,
  min_observations=2}`.
- **Без float в деньгах**: веса — целые микросекунды (точные int из часов), midpoint — Decimal;
  единственный иррациональный шаг (stdev sqrt) в фикс. контексте prec=50. Вырожденный случай
  (все на один момент, нулевой dwell) → равновесное среднее.

### Verified
- `cd quant && uv run pytest tests/test_fair_value.py -q` → **8 passed** (worked examples: равный
  dwell → TWAP 0.50 / σ 0.10 / p_lo 0.30; неравный dwell → взвешивание по времени, σ 0.1732;
  константа → σ=0; p_lo клампится в 0; <min_observations → None; configurable min; as_of до
  последнего тика → ValueError; вырожденный same-timestamp → simple mean).
- `cd quant && uv run pytest -q` → **288 passed** (было 280; +8; без регрессий).

### Decisions locked this slice
- **TWAP + k·σ нижняя граница** как первый слой (по решению пользователя): просто, консервативно,
  опирается на уже хранимые quotes (+ trades VWAP из A1 при желании). `p_hat` = рыночный консенсус,
  сглаженный по времени; `p_lo` — то, что тестирует gate (CLAUDE.md: size на p, gate на нижней).
- **None при нехватке данных** (никогда не фабрикуем точечную оценку). Калибровка/замена модели —
  после A5 (resolution journal).

## Slice: Resolution-watcher → calibration → live Kelly  ✅ done (2026-06-17)

Roadmap Трек A5: замыкает петлю честности. Детектим разрешившиеся отслеживаемые рынки на Gamma,
матчим к последней направленной оценке, пишем строку в `calibration` journal, и — главное —
**подаём усохшую (shrink-only) Kelly-долю из калибровки в живой sizing** (раньше journal был
display-only на странице Calibration).

### What's done
- **Pure resolution** (`app/ingestion/resolution.py`): `resolved_outcome(outcomes, prices)` —
  realized YES-исход бинарного Yes/No рынка (1/0/None; definitive только при ценах {"0","1"},
  yes-индекс уважается даже при перестановке); `calibration_from_resolution(signal, outcome, at)`
  → `CalibrationRecord` (estimate=`fair_value`, price=`price`). Только `extreme_correction` (несёт
  вероятность); arb/longshot пропускаются.
- **Client** (`app/polymarket/gamma_client.py`): `fetch_resolutions(condition_ids)` →
  `/markets?closed=true&condition_ids=...` (разрешённые среди отслеживаемых; pending просто
  отсутствуют). Подтверждено на реальном API.
- **Watcher** (`app/ingestion/resolution_engine.py`): `run_resolution_scan_once` (seam) — грузит
  вселенную + последний directional-сигнал на рынок + уже-записанные market_id, фетчит резолюции,
  пишет calibration для каждого нового. **Идемпотентно** (повторный прогон не дублирует). CLI.
- **Live Kelly wiring** (`app/api/signals.py`): `effective_kelly_frac(session, settings)` —
  `summarize(journal, base_frac=kelly_frac).kelly.adjusted_frac` или фолбэк на `kelly_frac` при
  пустом journal / отсутствии high-confidence доказательств. `_enrich` теперь сайзит на этой доле.
  **Shrink-only** (≤ kelly_frac): калибровка может только снизить риск.

### Verified
- `cd quant && uv run pytest -q` → **301 passed** (было 288; +13: 6 pure resolution worked-examples
  — yes/no/flip/pending/non-binary/record; 3 engine orchestration — journal-with-prediction /
  already-journaled-skip / no-prediction-skip; 2 wiring — пустой journal→fallback, overconfident
  10×p=0.8 6 wins → frac 0.25→**0.1875**; 2 client request/parse). Без регрессий.

### Money-math / correctness decisions (carry forward)
- **Live Kelly = калибровочно-усохшая**: `adjusted_frac ≤ kelly_frac` всегда (shrink-only,
  переиспользует `suggest_kelly_fraction`). Пустой journal / нет high-conf → конфигурный `kelly_frac`.
- **Идемпотентность через journaled-set** (market_id уже в `calibration` для стратегии → пропуск);
  `calibration` append-only без уникального ключа — повторная запись исключена на уровне воркера.
- Только `extreme_correction` журналируется (несёт `fair_value`); arb risk-free, longshot без `p`.

### Decisions locked this slice
- Резолюция из `closed=true&condition_ids=` (pending рынки просто не возвращаются — не нужен
  отдельный «recently-resolved» источник). Outcome из `outcomePrices` {"0","1"}.
- Воркер — отдельный CLI `python -m app.ingestion.resolution_engine` (зеркало signals/engine).

## Slice: Price-signal live scanner (longshot + correction)  ✅ done (2026-06-17)

Roadmap Трек A6: завести две чистые price-функции (`math/longshot` + `math/correction`) в живой
скан. Раньше они были math+persistence-only (без scanner'а); теперь читают последний YES-midpoint
из `quotes` и персистят сигналы на цикле. Сетевых вызовов нет — потребляет квоты ingestion-поллера.

### What's done
- **Scanner** (`app/signals/price_engine.py`, зеркало `signals/engine.py`): `run_price_scan_once`
  (seam) — грузит вселенную + `load_latest_quotes` по YES-токенам, на каждый рынок гонит
  `evaluate_extreme_correction` + `evaluate_favourite_longshot` по midpoint, персистит двумя
  **гомогенными** батчами (`insert_signals` компилирует колонки из первой строки). `run_price_poller`
  + CLI `python -m app.signals.price_engine [once|loop]`; метрики `SIGNALS{strategy,scan}`.
- **Config knobs** (`app/config.py`): `EDGE_LONGSHOT_{LO,HI}`, `EDGE_FAVOURITE_{LO,HI}`,
  `EDGE_CORRECTION_{LO,HI,NUDGE_MIN,NUDGE_MAX}` (Decimal) → маппятся в `LongshotParams`/
  `CorrectionParams` (как `_params` для арбитража).

### Verified
- `cd quant && uv run pytest -q` → **305 passed** (было 301; +4 orchestration: deep-longshot
  midpoint 0.10 → оба сигнала; mid-range 0.50 → ничего; favourite 0.80 → только longshot;
  отсутствующий midpoint → пропуск). Без регрессий. Pure band/nudge — в test_longshot/test_correction.

### Decisions locked this slice
- **Без сети**: scanner читает уже хранимые `quotes` (последний midpoint), не дёргает CLOB.
- Каждая стратегия — свой батч `insert_signals` (требование гомогенности).
- **Замена эвристического `fair_value` (correction) на TWAP-оценку A4** — осознанный follow-up
  (меняет смысл персистируемого сигнала и источник `p_lo` в gate; требует решения по модели). Сейчас
  correction остаётся на своём nudge-эвристике; A4-модель доступна как отдельный источник `p`.

## Slice: Alert dedup/rate-limiting (A7, partial)  ✅ done (2026-06-17)

Roadmap Трек A7: монитор перевыпускал один и тот же алерт каждый цикл, пока условие держится —
шумно. Добавлен dedup/rate-limit. **Live equity feed для реального drawdown остаётся заблокирован
отсутствием исполнения** (нет удерживаемых позиций — advisor-only); это Трек C. Заметка: после A5
**calibration drift уже работает на реальных данных** (journal заполняется resolution-watcher'ом);
backtest-drawdown остаётся на `EDGE_BACKTEST_RESOLUTIONS_PATH` до появления исполнения/позиций.

### What's done
- **`AlertDeduper`** (`app/observability/alert_dedup.py`, pure+stateful): `filter(alerts, now)` —
  повтор того же `kind` публикуется только после `cooldown` с последнего emit; **re-arm** когда
  условие исчезает (цикл без этого kind сбрасывает состояние → свежий пробой алертит сразу). Cooldown
  меряется от последнего emit, не от последнего «виден».
- **Wiring**: `run_monitor_once` принимает опц. `deduper`; `run_monitor` владеет одним экземпляром
  через циклы. Кноб `EDGE_ALERT_COOLDOWN_S` (дефолт 3600).

### Verified
- `cd quant && uv run pytest -q` → **312 passed** (было 305; +7: 6 dedup worked-examples —
  first-emit / suppress-within-cooldown / re-emit-after / clock-from-last-emit / cleared-re-arm /
  distinct-kinds-independent; +1 monitor-loop интеграция: персистентный drawdown за 2 цикла →
  публикуется один раз). Без регрессий.

### Decisions locked this slice
- **Real live-equity drawdown отложен до исполнения (Трек C)** — без позиций «реального P&L» не
  существует; честно задокументировано, монитор остаётся на backtest-реплее для drawdown.
- Dedup: один экземпляр на loop, состояние в памяти процесса (монитор — единственный продьюсер этих
  двух алертов; durable-стейт не нужен).

## Трек A (замкнуть петлю советника) — ЗАВЕРШЁН (A1–A7)
A1 trades · A2 category · A3 fees · A4 fair-value · A5 resolution→calibration→live-Kelly ·
A6 price-signal scanner · A7 alert dedup. Советник самодостаточен по данным/математике; реальный
equity-feed (drawdown по факту) ждёт исполнения (Трек C). Сьют **245 → 312 passed** (+67 за сессию).

## Slice: Production hardening — Docker + CI + API auth (Трек B: B1–B4)  ✅ done (2026-06-17)

Roadmap Трек B (эксплуатация): контейнеризация, CI, и закрытие открытого quant API. Делается
параллельно с A; B4(секреты) — предпосылка для Трека C.

### B1 — Контейнеризация
- **Dockerfiles**: `quant/Dockerfile` (multi-stage uv; дефолт — uvicorn, поллеры через
  `command:`), `web/Dockerfile` (Next **standalone** через corepack pnpm@9.15.0, slim runner,
  non-root), `executor/Dockerfile` (uv; pure-core, без сервера — заглушка до фаз signer/relay).
  `+ .dockerignore` на каждый. `next.config.ts` → `output: "standalone"`.
- **Full-stack compose** `infra/docker-compose.full.yml`: postgres+redis + one-shot `migrate` +
  `quant-api` (healthcheck) + поллеры (`scanner`/`signals`/`price-signals`/`trades`/`monitor`) +
  `web`. YAML-anchor общих env; `depends_on` по health/completed. **`docker compose config` →
  VALID**; web env-имена (`QUANT_API_URL`/`REDIS_URL`) совпали с `lib/env.ts`. (Полная сборка
  образов — в CI/локально с сетью; in-sandbox build base-pull завис, прерван — код проверен иначе.)

### B2 — CI/CD
- `.github/workflows/ci.yml`: 3 джобы — **quant** (postgres+redis services, uv sync, alembic
  upgrade, pytest incl. DB-gated), **executor** (postgres service, pytest DB-gated), **web**
  (node22 + corepack pnpm, `tsc --noEmit`, vitest, `next build`). **Без новых зависимостей.**
  Линт (ruff) + coverage — отложены (новые dev-deps; CLAUDE.md требует согласования — см. ниже).

### B3 — Auth + CORS + rate limit (закрыли открытый API)
- **`app/api/security.py`**: `require_api_key` (при `EDGE_API_KEY` advisor-маршруты требуют
  `X-API-Key`; `/health`,`/metrics` никогда не гейтятся), `RateLimiter` (per-IP sliding-window,
  чистая логика), `cors_origins`. Wired в `main.py`: CORS-middleware (origins из
  `EDGE_CORS_ORIGINS`), rate-limit middleware (при `EDGE_RATE_LIMIT_PER_MIN>0`), `require_api_key`
  на роутеры. Web BFF (`lib/api/client.ts` + `env.ts`) шлёт `X-API-Key` из `QUANT_API_KEY` когда задан.
- Кнобы: `EDGE_API_KEY` (секрет), `EDGE_CORS_ORIGINS`, `EDGE_RATE_LIMIT_PER_MIN`, `EDGE_ALERT_COOLDOWN_S`.

### B4 — Секреты
- Политика задокументирована (`quant/.env.example` + executor): **ни один секрет не коммитится**;
  `EDGE_API_KEY`/`EDGE_SENTRY_DSN`/DB-creds/(будущие KMS) приходят из env, инжектятся secret-
  менеджером (Vault/AWS SM/Doppler) в проде; `config.py` читает через pydantic-settings; `.env`
  в `.gitignore`. (Реальная интеграция конкретного менеджера требует аккаунта — вне автономной части.)

### Verified
- `cd quant && uv run pytest -q` → **319 passed** (+7 security: rate-limit окно/per-key, auth
  open/reject/accept, CORS csv). TestClient: `/signals` без ключа → **401** (короткое замыкание до
  DB), `/health` → 200. `cd web && … tsc --noEmit` чисто; `… test` → **79 passed**. `docker compose
  -f infra/docker-compose.full.yml config` → VALID.

### Заблокировано (нужно решение/доступ — см. конец файла)
- **B2-lint / B5 (coverage + e2e)**: новые dev-deps (ruff, pytest-cov, @playwright/test) — CLAUDE.md
  требует согласования перед добавлением. Ждёт «да».
- **Трек C (исполнение, Фазы 4–7)**: реальные AWS KMS / Polygon-Amoy ключ / private-relay +
  лимиты капитала — внешние доступы и необратимые решения; автономно не делается.

## Slice: Quality tooling — ruff lint + coverage + e2e (Трек B5)  ✅ done (2026-06-17)

Roadmap Трек B5 (dev-deps согласованы пользователем): линт, покрытие, e2e.

### What's done
- **Ruff (lint-only)** на quant + executor: `[tool.ruff.lint]` select E/W/F/I/UP/B/C4, ignore
  E501/C408/B008. **Формат НЕ навязываем** (код в осознанном компактном hand-стиле — CLAUDE.md
  «match surrounding»). Авто-фикс применён (UTC-alias, сортировка импортов). Ruff **нашёл реальный
  латентный баг**: `app/math/arb.py` использовал `datetime` в аннотациях без импорта (работало лишь
  через `from __future__ import annotations`) — импорт добавлен; `fair_value` zip'ы → `strict=True`.
- **Coverage** (pytest-cov): quant **84%**, порог CI `--cov-fail-under=80`.
- **Playwright e2e** (web): `@playwright/test` + `playwright.config.ts` (webServer = `next` бинарь
  напрямую, минуя pnpm-лаунчер — система pnpm11 ломается на Node20) + `e2e/dashboard.spec.ts` (shell
  рендерится на `/` и `/signals` без backend — устойчиво к no-Redis/no-quant). 
- **CI** (`.github/workflows/ci.yml`): + lint + coverage в quant/executor джобах; новый шаг e2e
  (build → playwright install chromium → e2e) в web-джобе.

### Verified
- `cd quant && uv run ruff check app tests` → **All checks passed**; `… pytest -q --cov` → **319
  passed, 84%**. `cd executor && … ruff check` → passed; `… pytest -q` → **37 passed**. 
- **Playwright e2e (реальный Chromium)**: `… playwright test` → **3 passed (2.9s)** — shell на
  home + signals рендерится end-to-end (alerts-SSE ошибки = ожидаемое no-Redis состояние, страницы
  его терпят by design).

## Трек B (production-эксплуатация) — ЗАВЕРШЁН (B1–B5)
B1 Docker+compose · B2 CI · B3 auth/CORS/rate-limit · B4 secrets-policy · B5 lint/coverage/e2e.

## Коммиты этой сессии (ветка feat/advisor-loop-and-ops)
`feat(executor): pure-core` · `feat(quant): advisor loop A1–A7 + auth` · `feat(ops): docker/CI/auth`
· `docs(progress)` · `chore(quality): ruff/coverage/e2e`. (Track A 245→312 тестов; +security 319;
executor 37; web 79 unit + 3 e2e.)

## Slice: Execution Phase 4 (offline signer core) — policy + crypto + approval  ✅ done (2026-06-17)

Трек C, Phase 4 против **локального testnet-ключа** (без реального KMS/mainnet). Центральный
вывод threat-model: считать executor скомпрометированным — signer имеет СОБСТВЕННУЮ default-deny
политику и подписывает только то, что прошло. Три слайса:

### Signer policy (pure, no keys) — `app/signer/policy.py`
`evaluate_policy(envelope, policy, now, approval_valid)` — re-verify `intent_hash`, chainId-пин,
expiry, action-allowlist, contract-allowlist, per-tx notional + slippage cap, **exact (никогда
infinite/zero) ERC-20 allowance** allowlisted-спендеру, approval-gate выше порога. **Default-deny**:
всё нераспознанное запрещено; возвращает ВСЕ причины. 15 worked-example тестов.

### Signer crypto (local key) — `app/signer/{eip712,crypto}.py`
`LocalSigner` (eth-account; оффлайн-замена AWS KMS за тем же `address`/`sign` интерфейсом) подписывает
**реальный EIP-712** месседж, связывающий `intent_hash` (SHA-256 над всем интентом) + on-chain
границы (chainId/expiry/nonce). `recover_signer` round-trip'ит адрес. Ключ НИКОГДА не в выводе.
9 тестов (round-trip recovered-address==signer, no-key-leak, different-intent→different-sig).

### Approval token — `app/signer/approval.py`
HMAC, связанный с ТОЧНЫМ `intent_hash` + TTL. Signer верифицирует сам: ниже порога — не нужен;
выше — токен для ДРУГОГО интента не разблокирует этот (стоп approve-cheap-swap-expensive);
expired/tampered/wrong-secret отклоняются. 6 тестов.

### sign_intent (gated entrypoint) — `app/signer/service.py`
Policy-ЗАТЕМ-sign: запрещённый/подделанный/выше-порога-без-токена интент → **НЕТ подписи**.

### Verified
- `cd executor && uv run pytest -q` → **67 passed** (52→67: +15 policy, +9 crypto, +6 approval).
  Ruff clean. Известный Anvil test-ключ деривит ожидаемый адрес; EIP-712 sign→recover round-trip OK.
- Депы: `eth-account` (runtime, для signer). Кноб `EDGE_EXEC_SIGNER_PRIVATE_KEY` — **testnet-only
  секрет**, прод использует `kms_key_id` (ключ не экспортируется).

### POST /sign HTTP-сервис — `app/signer/main.py` ✅
Минимальный FastAPI (отдельный изолированный deployable), единственная ручка POST /sign:
re-runs политику + верифицирует approval-token, подписывает локальным ключом только при allow,
иначе 403 + reasons; 503 без ключа. `policy_from_settings`/`signer_from_settings` строят политику +
ключ из `EDGE_EXEC_*`. 5 TestClient-тестов. Депы: `fastapi`/`uvicorn` (+ httpx dev).

### Phase 4 ЗАВЕРШЁН (offline): policy + EIP-712 crypto + approval + /sign против локального ключа.
**Осталось — Phase 5+ (relay/mainnet/KMS)**: внешние доступы/необратимые решения (ниже).

## Осталось — Трек C (исполнение, Фазы 5–7)
Phase 4 (signer security core: policy + EIP-712 crypto + approval, против локального test-ключа)
**ЗАВЕРШЁН** (выше). Остаётся:
- **POST /sign HTTP-сервис** — тонкая deployable-обёртка над `sign_intent` (грузит ключ/политику из
  config); ядро готово.
- **C0/C5–C7**: реальный приватный relay на Polygon + точные адреса контрактов Polymarket; AWS KMS
  (замена локального ключа, sign-only, ключ не экспортируется); Gnosis Safe холодный сплит; testnet
  E2E → capped mainnet-канарейка за `EDGE_EXEC_ENABLED` + лимиты капитала. **Внешние доступы и
  необратимые денежные решения** — вне автономной части.

## Slice: Personalization layer — $-earnings, risk, config, portfolio  ✅ done (2026-06-17)

The personal-advisor slice the operator asked for: "how much will I earn, what's the risk,
filter the safest, my bankroll, track what I placed." Advisor-only (no execution). Built in
10 small slices (A–J), each tested + committed on `feat/advisor-loop-and-ops`.

**Money-math decisions (non-negotiable, see CLAUDE.md):**
- New pure module `quant/app/math/profit.py` is the dollar view; its win/loss payoff is
  **cross-checked in tests against `math/backtest.py::realized_pnl`**, so "expected earnings"
  on the dashboard and realized P&L in the replay can never drift.
- EV cost basis is the **all-in `threshold`** (`m + half_spread + slippage + gas`), matching the
  backtest fill — NOT the displayed `edge` price. `ev_usd` uses mean `p`; `ev_usd_conservative`
  uses the gated CI lower bound `p_lo` (clamped to [0,1]).
- Formulas: `shares = stake/ask`; `profit_if_win = stake*(1-ask)/ask`; `profit_if_loss = -stake`;
  `expected_value = p*profit_if_win - (1-p)*stake`; `prob_of_loss = 1-p`; arb locked profit =
  `net_edge*set_size`; `settled_pnl(side,stake,ask,outcome)` mirrors the directional payoff;
  `mark_to_market = shares*current_mid - stake`.
- **Only set-arb is risk-free** — surfaced explicitly (risk-free badge, `prob_of_loss=0`);
  everything else is +EV with an honest `prob_of_loss` and win/loss $ spread.

**Backend (quant):**
- A: `math/profit.py` + `tests/test_profit.py` (24 worked examples).
- B: `Economics` model on `AdvisedSignal`; `advisor/view.py` fills it for directional + arb.
- C: `advisor/ranking.py` (safety tiers: arb→0, gate-passing directional→1, rest→2);
  `GET /signals` gains `sort=net_edge|safety`, `safe_only`, `min_net_edge`.
- D: `user_config` table + migration `0006`, `models/config.py::UserConfig` (env-defaulted,
  range-validated), store `load/upsert_user_config`, `GET/PUT /config`. Signals now size off the
  persisted config (bankroll, kelly_frac base, kelly_cap); `?bankroll=` stays an override.
- E: `positions` table + migration `0007`, `models/position.py`, store
  `insert/load/settle_position`, `POST/GET /positions` (live unrealized P&L + totals).
  Resolution-watcher gains `run_position_settlement_once` (idempotent, directional only).

**Web (Next.js):**
- F: `EconomicsSchema` on the signal; new `config.ts`/`position.ts` schemas; client gains
  `SignalsQuery`, `get/updateConfig`, `get/createPosition` (+ POST/PUT helper); BFF
  `/api/config`, `/api/positions`.
- G: Signals table swaps raw `p` for **Expected $** + **Loss risk** columns; detail page gains an
  Earnings & risk card.
- H: home page rewritten — live "best bets, safest first" (`safe_only&sort=safety`), real stats
  (bankroll-at-risk), SSE-merged. AppShell nav: Portfolio + Settings.
- I: `/settings` page — bankroll input + fractional-Kelly / cap / corr-cap / max-loss-prob
  sliders (`useConfig` hook); saving re-sizes every recommendation.
- J: `/portfolio` page — positions table with live/realized P&L + totals; `RecordBetForm`
  (POST, prefilled from a signal's "Track this bet" deep-link).

**Files added:** `quant/app/math/profit.py`, `quant/app/advisor/ranking.py`,
`quant/app/models/config.py`, `quant/app/models/position.py`, `quant/app/api/config.py`,
`quant/app/api/positions.py`, `quant/alembic/versions/0006_user_config.py`, `0007_positions.py`,
and tests; `web/src/lib/schemas/{config,position}.ts`, `web/src/lib/useConfig.ts`,
`web/src/app/{settings,portfolio}/*`, `web/src/app/api/{config,positions}/route.ts`.
**Files modified:** `quant/app/{models/advisor,advisor/view,api/signals,ingestion/store,
ingestion/resolution_engine,db/tables,main}.py`; `web/src/lib/{schemas/signal,api/client,format}.ts`,
`web/src/app/{page,signals/SignalsTable,signals/[id]/page}.tsx`, `web/src/components/AppShell.tsx`.

**Verification:** `cd quant && uv run pytest -q` → 359 passed (with `EDGE_TEST_DATABASE_URL`,
0 skips); `uv run ruff check app tests` clean; `alembic upgrade head` applies 0006+0007.
`cd web && pnpm test` → 79 passed; `tsc --noEmit` clean; `pnpm build` passes for all routes
(`/portfolio`, `/settings`, `/api/config`, `/api/positions`).

**Next (Part 2 — semi-auto execution, deferred):** executor Phases 5–7 (testnet dry-run relay →
approval UI → mainnet canary behind `EDGE_EXEC_ENABLED` + human sign-off). Real money/irreversible
— gather custody + limits requirements first.

## Slice: Execution Phase 5a — dry-run pipeline  ✅ done (2026-06-17)

Part 2 (semi-auto execution) кикофф. Operator chose: **dry-run first, local testnet key,
every trade manual**. This slice is the safe, fully-autonomous milestone — the whole chain
runs end-to-end but **nothing reaches a network**.

- **Config** (`executor/app/config.py`): `EDGE_EXEC_DRY_RUN` (default **true**) and
  `EDGE_EXEC_REQUIRE_APPROVAL_FOR_ALL` (default **true** — manual confirmation per trade).
- **`executor/app/relay/client.py`** — `submit(intent, signed, *, dry_run)`: dry-run records the
  float-free order payload (Decimals as strings) + returns it for the audit; **live submission
  raises `NotImplementedError`** (Polymarket CLOB needs a provider-specific signed-order schema +
  API credentials — external; we refuse to guess a wire format with real money).
- **`executor/app/orchestrator/pipeline.py`** — `execute_signal()` composes the tested pure
  pieces: allocate nonce → `intent_from_signal` → persist + audit `formed` → `breakers.evaluate`
  → approval gate → `signer.sign_intent` → `relay.submit` (dry-run) → audit `submitted`.
  Dependency-injected (session/signer/policy/limits/state/clock/ids) like the advisor engines.
- **Two safety properties pinned by tests:** (1) dry-run never broadcasts; (2) semi-auto blocks
  any unsigned trade — every trade needs a valid HMAC approval token bound to the exact intent
  hash, else the flow stops at `pending_approval` with no signature. Dry-run is exempt from the
  master switch (it's a simulation); a real run still requires `EDGE_EXEC_ENABLED`.

**Files:** new `executor/app/relay/{__init__,client}.py`, `executor/app/orchestrator/pipeline.py`,
`executor/tests/test_relay_client.py`, `executor/tests/test_pipeline.py`; modified
`executor/app/config.py`. **Verify:** `cd executor && uv run pytest -q` → 80 passed (with
`EDGE_EXEC_TEST_DATABASE_URL`), ruff clean.

**Next (Phases 5b–7, external deps / money decisions):** real Polymarket CLOB order schema +
EIP-712 domain + API L2-auth (live submit on testnet); approval-workflow web UI (render intent,
issue single-use TTL token); a Redis consumer/CLI to drive the pipeline from `edge:signals`;
then AWS KMS signer + Gnosis Safe + capped mainnet canary behind `EDGE_EXEC_ENABLED` + sign-off.

## What's next
- **Streaming, next steps**: emit a "removal"/staleness event when a live arb edge clears so the
  dashboard row drops (today it lingers); extend the live re-eval beyond arb to the directional
  path once it has a live `p` source. The arb `/api/stream` SSE over Redis + the live Signals-table
  merge are **done** (above). Optional: swap the hand-written Zod schemas for
  `openapi-typescript`-generated types off the FastAPI OpenAPI spec.
- **Calibration wiring**: a resolution-watcher that detects resolved markets, matches each to its
  prior estimate(s), and writes `calibration` rows; then feed `suggest_kelly_fraction`'s
  `adjusted_frac` into the **live sizing knobs**. The Calibration/Backtest **dashboard pages are
  done** (above) — they consume the typed client and surface the suggested fraction prominently,
  but display-only; closing the loop into sizing is the remaining work. Depends on
  trades/resolution ingestion + the fair-value `p` source.
- **Trades ingestion**: Data API `/trades` client + `trades` hypertable + poll trade prints.
- **Category resolution**: Gamma `/markets` frequently omits `category` (observed NULL in the
  smoke run). Derive it from `events[].tags[]` so the fee table (crypto/politics/…) can key on it.
- **More signals math**: fee-by-category table, model fair-value + CI-lower-bound gate. (Set-arb
  `math/arb.py` + `signals/engine.py` done; price signals `math/longshot.py` +
  `math/correction.py` done; bet-sizing `math/bet_sizing.py` done — fractional Kelly + caps.)
- **Sizing wiring**: feed the fair-value model's `p` (mean) + CI `p_lo` (lower bound) into
  `position_size`, attach the proposed stake to each gated signal, and add `EDGE_KELLY_*` knobs
  (`frac`/`cap`) + a per-tag bankroll cap for `cap_correlated_stakes` — depends on the fair-value slice.
- **Price-signal scanner**: wire the two new pure functions to a live scan — read the latest
  midpoint per market from `quotes`, evaluate, and persist on a loop with a CLI (mirrors
  `signals/engine.py`); add the `EDGE_*` knobs for their bands/nudge then. Feed `fair_value`
  into the future fair-value/Kelly gate.
- **Signals plumbing**: publish signals to Redis for the phase-4 SSE stream; merge the signal
  scan into the ingestion cycle to **reuse the books it already fetches** (drop the standalone
  re-fetch). (`GET /signals` now serves the persisted signals enriched with sizing — above.)
- **Backtest, next steps**: feed real `resolutions` from the resolution-watcher (above); add
  re-entry/exit policies and depth-aware arb once a full-book history exists; wire
  `favourite_longshot` once it has a probability source. (`GET /backtest` now degrades to an
  empty report; a `POST` with a body is the path once a resolution feed exists.)

- **Observability, next steps**: real-time drawdown needs a **live equity feed** (today it runs off
  the backtest replay) and calibration drift needs the **resolution-watcher** to keep the journal
  fresh; add **alert dedup/rate-limiting** (the monitor re-fires every cycle while a condition holds);
  optionally add `@sentry/nextjs` for browser error capture and a Prometheus/Grafana scrape stack
  pointed at `/metrics`. (Structured logging, Sentry, `/metrics`, and the three app-level alerts +
  dashboard toasts are **done** — above.)

## Open questions / observations
- **Signal ids are synthesized** (`strategy:market_id:epoch_ms`) because `signals` has no PK. Fine
  at the 15s scan cadence, but add a surrogate `id` column (the table is *regular*, so a real PK
  is feasible) before signals are referenced anywhere durable (e.g. journaling a clicked bet).
- The advisor enriches signals at **request time** off the latest quote; a stale `signals` row
  whose market has since moved is sized against the *current* quote (honest for a live advisor,
  but the displayed `time` is the detection time). Re-detection cadence / TTL is unaddressed.
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
