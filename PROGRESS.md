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

## What's next
- **Streaming (phase 4)**: the remaining web data-wiring piece — `/api/stream` SSE over Redis
  (ioredis) pushing live signal updates, and a client subscription that re-renders the Signals
  table in place. BFF `/api/signals(+/[id])` + Zod boundary + typed client + the Signals page &
  detail view are **done** (above). Optional: swap the hand-written Zod schemas for
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
