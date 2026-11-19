# syntax=docker/dockerfile:1
# One image, two roles on Cloud Run:
#   API    (service):     default CMD
#   worker (worker pool): command override `python -m app.worker`
# Both stages must use the same Python image: the venv's interpreter symlinks into it.
ARG PYTHON_IMAGE=python:3.12-slim

# --- Stage 1: resolve and install dependencies into /app/.venv ---
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies only, so this layer is reused until uv.lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# --- Stage 2: runtime (no uv, no build cache, non-root) ---
FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
# Cloud Run sets $PORT (8080). exec so uvicorn gets SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
