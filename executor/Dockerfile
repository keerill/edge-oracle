# EdgeOracle execution module. Multi-stage uv build.
# This slice is pure-core (no server entrypoint yet); the image is built so the gated
# executor/signer services can be deployed in later phases. Default command is a no-op shell.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# No long-running entrypoint until the signer/relay phases land. Keep EDGE_EXEC_ENABLED=false.
CMD ["python", "-c", "print('executor image: pure-core, no server yet (EDGE_EXEC_ENABLED gated)')"]
