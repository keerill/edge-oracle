# EdgeOracle quant service (FastAPI + pollers). Multi-stage uv build.
# Default command runs the API; the pollers/CLIs override `command:` in compose.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (cached) from the lockfile, without the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Then the source + the project install.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# Advisor-only API. EDGE_RUN_POLLER_ON_STARTUP stays false; run pollers as separate services.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
