## Multi-stage build. The first stage uses the official `uv` image to install
## the project's dependencies into a virtualenv; the second copies that venv
## into a slim runtime image. Final image weighs ~150 MB and runs on linux/arm64
## (Pi 4 / Pi 5) and linux/amd64.

ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install deps only (cached layer). The project itself has no [build-system],
# so we don't try to install it — the runtime image puts yoto_bridge/ on the
# PYTHONPATH directly. uv sync needs the README to satisfy pyproject metadata.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev


FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

# tzdata is required for the TZ env var to resolve named zones (otherwise
# Python silently falls back to UTC and routines/events fire an hour off
# during BST). Slim images don't ship it by default.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# Run as non-root. user_data/ is created/owned via bind-mount on the host.
RUN useradd --create-home --uid 1000 yoto

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    YOTO_USER_DATA_DIR=/data \
    YOTO_TOKEN_FILE=/data/yoto_tokens.json \
    YOTO_SCHEDULE_FILE=/data/schedule.json \
    YOTO_EVENTS_FILE=/data/events.json \
    YOTO_BRIDGE_HOST=0.0.0.0 \
    YOTO_BRIDGE_PORT=8765

COPY --from=builder --chown=yoto:yoto /app/.venv /app/.venv
COPY --chown=yoto:yoto yoto_bridge ./yoto_bridge

USER yoto
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "-m", "yoto_bridge"]
