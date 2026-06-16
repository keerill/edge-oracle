# EdgeOracle — Polymarket quant advisor

## What this is

An ADVISOR that finds quantitative edges on Polymarket and surfaces them for a human to act on.
It does NOT auto-execute trades or hold wallet keys. (Execution is a separate, later, gated module.)

## Architecture

- `quant/` Python FastAPI service: ingestion, fair-value models, signal scanners, Kelly sizing, backtest.
- `web/` Next.js (App Router) + TypeScript advisor dashboard (neon design system, SCSS Modules).
- `infra/` docker-compose: Postgres + TimescaleDB (tick history) and Redis (cache + pub/sub).
- Layers are independent; they talk over an internal API + Redis, not a shared codebase.

## Commands (Claude can't guess these)

- Stack up: `docker compose -f infra/docker-compose.yml up -d`
- Quant dev: `cd quant && uv run uvicorn app.main:app --reload`
- Quant tests: `cd quant && uv run pytest -q` <- the PRIMARY check; run after every change
- Web dev: `cd web && pnpm dev`
- Web tests: `cd web && pnpm test`
- DB migration: `cd quant && uv run alembic upgrade head`

## Workflow rules

- Explore -> plan -> implement -> commit. For multi-file or unclear work, show the plan FIRST.
- Run the relevant tests after every change and paste the output. "Looks done" is not done.
- Commit each working unit with a descriptive message. Keep commits small and reversible.
- At the end of a session, update PROGRESS.md (what's done, what's next, open questions).
- When compacting, always preserve: the list of modified files, test commands, and any money-math decisions.

## Code style

- Python: type hints everywhere, Pydantic v2 models, pure functions for all math (no I/O inside math).
- TS: strict mode, Zod for runtime validation at boundaries.
- Keep it simple. Do NOT add abstraction, libraries, or infra beyond the current task. Ask before adding deps.

## IMPORTANT — money math (correctness is non-negotiable)

- Kelly fraction for a YES share bought at price m with your probability p: f\* = (p - m) / (1 - m).
- ALWAYS apply fractional Kelly (default 0.25) AND a hard cap (default 5% of bankroll per position).
- The price m used in sizing is the price you PAY (the ask incl. half-spread), never the midpoint.
- Gate every bet on: p_lo > m + half_spread + slippage + gas + model-error margin (use the CI lower bound, not the mean).
- Every math function MUST have unit tests with worked numeric examples before it is used anywhere.

## IMPORTANT — security

- NEVER put private keys, API secrets, or seed phrases in code, env files committed to git, or the DB. Use a secret manager.
- NEVER write code that auto-executes trades unless a task explicitly says "execution module" — default is advisor only.
- Validate all external/API input (Pydantic/Zod). Treat Polymarket API responses as untrusted data.

## Out of scope (do not build unless a prompt explicitly asks)

- Auto-execution / wallet signing · Kafka · Kubernetes · ClickHouse · multi-exchange routing.
