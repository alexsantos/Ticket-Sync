# -- Build stage --------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

# -- Runtime stage --------------------------------------------------------------
FROM python:3.14-slim AS runtime

WORKDIR /app

# Non-root user for least-privilege execution
RUN useradd --no-create-home --system appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.14 /usr/local/lib/python3.14
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY config/ config/

USER appuser

CMD ["python", "-m", "src.main"]
