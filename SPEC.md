# EdgeOracle — v1 Specification

EdgeOracle is a Polymarket quant **advisor**: it finds quantitative edges and surfaces them for a
human to act on. It does **not** auto-execute trades or hold wallet keys (per `CLAUDE.md`).
Execution is a separate, later, gated module — out of scope here.

This document is the self-contained spec for **v1**: a thin end-to-end "walking skeleton" through
every layer, built and verified as one slice.

## Decisions locked

| Decision | Choice |
|---|---|
| Edge source | **Cross-market arbitrage** — internal Polymarket no-arb constraints |
| Arb flavor | **Binary YES+NO complement first**, model designed to generalize to N outcomes |
| v1 scope | **Walking skeleton** — one thin slice through every layer, verified end-to-end |
| Polymarket APIs | **Gamma** (discovery/metadata/resolution) + **CLOB REST** (book/price), polling |
| Tick storage | **Top-of-book quotes + trade prints** in TimescaleDB hypertables |
| Web ↔ quant | **REST + SSE via Next.js BFF**; Redis pub/sub feeds the SSE stream; browser never hits quant |
| Type contract | **OpenAPI → generated TS types** (`openapi-typescript`) + Zod at the boundary |
| Testing | CLAUDE.md baseline **+ Hypothesis property tests + recorded API fixtures** |
| Backtest | **Forward-collected deterministic replay** over stored ticks |
| Sizing/slippage | **On-demand full-book snapshot** at eval time → size-weighted executable price; gate on it |
| Dashboard | **Live edges list + detail breakdown**, no position/PnL tracking |

Fixed upstream by `CLAUDE.md` (not re-litigated): uv (Python) + pnpm (TS); fractional Kelly 0.25 &
5% bankroll cap defaults; advisor-only; neon design system + SCSS Modules; no
Kafka/k8s/ClickHouse/multi-exchange.

## 1. What v1 is

A live loop that polls a small tracked set of Polymarket binary markets, stores their top-of-book
history, detects **complementary mispricing** (YES + NO costing less than $1.00 after fees), sizes
the underpriced leg with gated fractional Kelly, and shows the ranked opportunities — with a full
money-math breakdown — on a Next.js dashboard that updates live. A deterministic replay backtester
runs the same engine over recorded ticks.

Non-goals for v1 are in §10.

## 2. Architecture & data flow

```
Polymarket (Gamma REST, CLOB REST — public, no auth)
        │  poll every SCAN_INTERVAL_S
        ▼
quant/ FastAPI service (uv)
  ingestion.scanner ──fetch full /book(YES),/book(NO)+trades──┐
        │ persist top-of-book + trades                        │ pass full books
        ▼                                                     ▼
  Postgres + TimescaleDB                              signals.engine
  (quotes, trades hypertables; markets table)         math.arb → math.slippage(VWAP)
        ▲                                             → math.kelly → math.gate
        │ replay                                              │ Signal
  backtest.replay (offline, deterministic)                   ▼
                                              Redis pub/sub  +  REST cache
                                              chan signals:live   GET /signals
        ┌──────────────────────────────────────────┴───────────────┐
        ▼ (SSE subscribe)                                           ▼ (server fetch)
web/ Next.js BFF  /api/stream  (EventSource → browser)     /api/signals, /api/signals/[id]
        ▼
  Dashboard: live ranked edge list → signal detail (money-math breakdown)
```

Layers talk over the internal REST API + Redis, never a shared codebase (per `CLAUDE.md`).

## 3. Repository layout (monorepo)

```
edge-oracle/
  SPEC.md  PROGRESS.md  CLAUDE.md
  infra/
    docker-compose.yml            # postgres+timescaledb, redis
  quant/                          # FastAPI service (uv)
    pyproject.toml  alembic.ini
    alembic/versions/             # 0001_init: markets, quotes/trades hypertables
    app/
      main.py                     # FastAPI app: CORS, routers, emits /openapi.json
      config.py                   # Pydantic Settings: DB/Redis URLs, kelly params, fee table, scan interval, margins
      api/
        routes_health.py          # GET /health
        routes_markets.py         # GET /markets, GET /markets/{id}
        routes_signals.py         # GET /signals, GET /signals/{id}
      models/                     # Pydantic v2 domain models = the API contract (source of truth)
        market.py  book.py  signal.py  sizing.py
      polymarket/                 # untrusted-input adapter; validate every response (Pydantic)
        gamma_client.py           # discover markets/events (httpx)
        clob_client.py            # get_book/get_price/get_midpoint/get_spread (httpx)
        schemas.py                # raw API response models (boundary validation)
        fees.py                   # per-category fee params + taker_fee_rate(price, category)
      ingestion/
        scanner.py                # poll loop: full books + trades → store top-of-book → run engine → publish
        store.py                  # write quotes/trades to Timescale
      math/                       # PURE functions, no I/O (CLAUDE.md). Every fn unit-tested.
        kelly.py  arb.py  slippage.py  gate.py  edge.py
      signals/
        engine.py                 # compose math into Signal candidates for a market
        publisher.py              # publish Signal JSON to Redis + cache latest for GET /signals
      backtest/
        replay.py                 # deterministic replay over stored ticks → signals
        metrics.py                # pure: realized edge, hit-rate, PnL
      db/ engine.py  tables.py    # SQLAlchemy async engine + table defs
    tests/
      test_kelly.py test_arb.py test_slippage.py test_gate.py test_fees.py
      test_polymarket_schemas.py test_engine.py test_backtest.py
      fixtures/ gamma_markets.json clob_book_yes.json clob_book_no.json clob_trades.json
  web/                            # Next.js App Router + TS strict (pnpm)
    package.json tsconfig.json next.config.ts .env.local.example   # QUANT_API_URL, REDIS_URL (server-only)
    src/
      app/
        layout.tsx page.tsx                       # dashboard: live ranked edge list
        signals/[id]/page.tsx                     # detail: money-math breakdown
        api/signals/route.ts                      # BFF → quant GET /signals (Zod validate)
        api/signals/[id]/route.ts
        api/stream/route.ts                       # SSE: subscribe Redis signals:live → browser
      lib/api/generated/                          # openapi-typescript output from quant /openapi.json
      lib/api/client.ts lib/api/schemas.ts        # typed server fetcher + Zod boundary schemas
      lib/format.ts                               # bps/price/money formatting
      components/SignalList.tsx SignalCard.tsx MoneyMathBreakdown.tsx GateBadge.tsx (+ .module.scss)
      styles/tokens.scss globals.scss             # neon design tokens
    tests/                                        # vitest + RTL: Zod schema + component render tests
```

## 4. Data model (TimescaleDB)

- `markets` (regular table): `market_id` PK, `event_id`, `question`, `category`, `yes_token_id`,
  `no_token_id`, `enable_order_book`, `active`, `closed`, `tracked` (bool), `created_at`.
- `quotes` (**hypertable** on `time`): `time timestamptz`, `token_id`, `market_id`, `best_bid`,
  `best_bid_size`, `best_ask`, `best_ask_size`. Index `(token_id, time desc)`.
- `trades` (**hypertable** on `time`): `time`, `token_id`, `market_id`, `price`, `size`,
  `taker_side` (nullable), `trade_id`.
- Migration `0001_init` creates tables then `SELECT create_hypertable('quotes','time')` / same for
  `trades`. Continuous-aggregate OHLC is **deferred** (§10).
- Prices/sizes stored as `NUMERIC` and handled as Python `Decimal` end-to-end (no float money math).

## 5. Polymarket adapter (untrusted input)

- **Gamma** `https://gamma-api.polymarket.com`: `GET /markets`, `GET /events`. Use `enableOrderBook`,
  `outcomes`==`["Yes","No"]`, `clobTokenIds` (→ `yes_token_id`/`no_token_id`), `category`, active/closed,
  resolution flags. `polymarket/schemas.py` validates the raw JSON before it touches domain logic.
- **CLOB** `https://clob.polymarket.com` (public reads): `GET /book?token_id=` (full depth — used for
  VWAP), `GET /price`, `GET /midpoint`, `GET /spread`, `GET /sampling-markets`. WebSocket
  (`wss://ws-subscriptions-clob.polymarket.com`, market channel) is **deferred**; v1 polls.
- Scanner fetches the **full** book per token each tick, persists only **top-of-book** for history,
  and passes the full books to the engine for depth-aware sizing (reconciles "store top-of-book,
  evaluate on full book").

## 6. Money-math (correctness is non-negotiable — `CLAUDE.md`)

All functions in `math/` are pure and unit-tested with worked numeric examples **and** Hypothesis
invariants before use.

**Fees** (`fees.py`) — per-dollar-of-cost taker fee, verified against published peak rates:
```
φ_cat(p) = feeRate_cat · (p·(1−p))^exp_cat       # peaks at p=0.5
fee_per_share(p) = p · φ_cat(p)
```
| category | feeRate | exp | peak effective (p=0.5) |
|---|---|---|---|
| crypto | 0.072 | 1 | 1.80% |
| politics | 0.040 | 1 | 1.00% |
| finance | 0.040 | 1 | 1.00% |
| sports | 0.030 | 1 | 0.75% |
| economics | 0.030 | 0.5 | 1.50% |
| geopolitical | 0.0 | — | 0% |
| _unknown_ | crypto rate | 1 | **most-conservative default** |

Gas ≈ 0 (relayer-subsidized); `gas_per_trade` config default 0.

**Executable price** (`slippage.py`): `executable_vwap(book_asks, target_shares) → (vwap, filled,
exhausted)` walks ask levels accumulating size; `m = vwap·(1 + φ(vwap))` is the price you PAY
(includes half-spread + slippage because it's depth-walked). Never assumes size beyond visible depth
(`exhausted=true` caps the recommendation).

**Complement-implied fair value** (`arb.py`, `edge.py`): a YES share + a NO share redeem for exactly
$1.00. So the fair value of YES implied by the hedge is `p̂_YES = 1 − a_n` where `a_n` is the NO
executable ask incl. fee (symmetrically for NO). The two-leg lock `a_y_incl + a_n_incl < 1` is the
case where **both** sides clear simultaneously. v1 surfaces the underpriced leg as the bet.

**Gate** (`gate.py`) — uses the **conservative lower bound**, never the mean (`CLAUDE.md`):
```
p_lo = p̂ − model_margin            # model_margin δ: latency + book-staleness haircut (config)
PASS  ⇔  p_lo > m + gas_per_trade   # m already includes half-spread, slippage, fee
```
Returns `GateResult(passed, reasons[])` so near-misses explain themselves.

**Sizing** (`kelly.py`): `f* = (p̂ − m)/(1 − m)`; then `fractional_kelly(f*, frac=0.25)` and hard
`cap=0.05` of bankroll; final size also capped at executable depth. Bankroll from config.

**Worked example (becomes a unit test, politics market):** `a_y=0.50, a_n=0.47`. φ(0.50)=0.01 →
`m_YES=0.505`; φ(0.47)=0.009964 → `a_n_incl=0.474683`; `p̂_YES=0.525317`; δ=0.01 →
`p_lo=0.515317` > `m=0.505` ⇒ **PASS** (+0.0103). `f*=(0.525317−0.505)/(1−0.505)=0.04104`;
×0.25 = **1.03%** of bankroll (5% cap not binding). These exact numbers become assertions.

## 7. API contract (Pydantic → OpenAPI → TS)

- `GET /health` → `{status}`
- `GET /markets` → `Market[]` (tracked universe)
- `GET /markets/{id}` → `Market`
- `GET /signals` → `Signal[]` (current edges, ranked by `edge_bps` desc; includes near-misses with `gate.passed=false`)
- `GET /signals/{id}` → `Signal`
- Live: engine publishes `Signal` JSON to Redis channel `signals:live`; web BFF subscribes for SSE.

```
Signal {
  id, market_id, question, side(YES|NO), edge_bps,
  recommended_size_shares, recommended_notional,
  gate: GateResult, money_math: MoneyMath, observed_at
}
MoneyMath {
  p_hat, p_lo, model_margin, executable_ask, fee_rate, fee, gas, total_cost,
  raw_kelly, fractional_kelly, cap_fraction, recommended_fraction, depth_limited
}
GateResult { passed: bool, reasons: string[] }
```

`web` runs `openapi-typescript` against `/openapi.json` → `lib/api/generated/`; `lib/api/schemas.ts`
holds Zod schemas validating the few consumed shapes at the BFF boundary (`openapi-zod-client` is an
option to auto-generate these — flagged as a dep decision).

## 8. Web (BFF + dashboard)

- `/api/signals` & `/api/signals/[id]`: server-side fetch `${QUANT_API_URL}`, Zod-validate, return.
- `/api/stream`: SSE route subscribes Redis `signals:live` (ioredis) and forwards `data:` events.
- Dashboard `page.tsx` (server component) renders initial `/api/signals`; a client `SignalList`
  opens `EventSource('/api/stream')` for live updates. `SignalCard` shows market, side, `edge_bps`,
  recommended size, `GateBadge`. Detail page renders `MoneyMathBreakdown` (every term in §6).
- Neon design system via `tokens.scss` + SCSS Modules. Strict TS; Zod at the boundary.

## 9. Testing (`CLAUDE.md` baseline + property tests + recorded fixtures)

- **quant** `uv run pytest -q` (primary check): worked-example unit tests for every `math/` fn;
  Hypothesis invariants — `f*` monotonic in `p̂` for `p̂>m`; recommended fraction ∈ `[0, cap]`; size
  never exceeds executable depth; gate impossible to pass when `p_lo ≤ m`; `φ` peaks at p=0.5 and the
  per-category peak rates match the table. Adapter parsing tested against recorded Gamma/CLOB JSON
  fixtures (deterministic, offline). `test_engine.py` runs the full engine on fixture books →
  asserts the §6 worked-example Signal. `test_backtest.py` replays a small recorded tick sequence →
  asserts golden metrics.
- **web** `pnpm test` (vitest + RTL): Zod schema accept/reject tests; `SignalCard`/`MoneyMathBreakdown`
  render tests.
- **Dependencies** (declared in the approved plan): quant — httpx, sqlalchemy[asyncio]+asyncpg,
  alembic, redis, pydantic-settings, hypothesis, pytest-asyncio; web — zod, openapi-typescript,
  ioredis, sass, vitest, @testing-library/react. Anything beyond this list is confirmed before adding.

## 10. Out of scope (v1)

From `CLAUDE.md`: auto-execution / wallet signing, Kafka, k8s, ClickHouse, multi-exchange routing.
v1-specific deferrals: WebSocket ingestion (REST polling only); full L2 storage (top-of-book stored,
full book fetched on-demand); N-outcome / linked-market arb (binary only; model built to generalize);
external-reference & microstructure models; paper-position / PnL portfolio tracking; historical
backfill; continuous-aggregate OHLC; auth / users / alerting. No secrets needed (CLOB reads public).

## Open questions / assumptions (non-blocking)

- **Tracked universe**: v1 uses a small config-driven seed list (a handful of liquid binary markets)
  rather than scanning all of Gamma. Revisit during the adapter step.
- **`model_margin` δ default**: proposed 0.01 (1¢); tune once live spreads are observed.
- **Fee table**: encoded from the published schedule and cross-checked to every peak rate; re-verify
  against `docs.polymarket.com/trading/fees` at implementation time as the canonical source.

## 11. End-to-end verification

1. `docker compose -f infra/docker-compose.yml up -d` — Postgres+TimescaleDB + Redis healthy.
2. `cd quant && uv run alembic upgrade head` — `markets` + `quotes`/`trades` hypertables created.
3. `cd quant && uv run pytest -q` — **all pass** (math, property, fees-vs-published-rates, fixtures,
   engine worked-example, backtest golden). Paste output.
4. `cd quant && uv run uvicorn app.main:app --reload` — `curl :8000/health` ok; `curl :8000/markets`
   shows the tracked universe; after one scan cycle `SELECT count(*) FROM quotes;` > 0; `curl
   :8000/signals` returns ranked edges, each with a full money-math breakdown and gate reasons.
5. `cd web && pnpm gen:api && pnpm test && pnpm dev` — dashboard at `:3000` lists live edges; opening
   a signal shows the breakdown; publishing a test message to Redis `signals:live` updates a card via
   SSE without reload.
6. `uv run python -m app.backtest.replay` over the fixtures prints PnL/hit-rate metrics matching the
   golden values.
7. **Money-math spot check:** pick one live signal and hand-verify `f*=(p̂−m)/(1−m)`, the 0.25
   fractional + 5% cap, the category/price fee, and that the gate used `p_lo` (not `p̂`).
