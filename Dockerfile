# Composer backend (FastAPI + LangGraph + Prisma) — Cloud Run image.
#
# Build:   docker build -t composer-backend .
# Run:     docker run -e DATABASE_URL=... -e JWT_SECRET=... -p 8080:8080 composer-backend
#
# The image is single-stage because Composer's deps are all pure-Python /
# pre-built wheels — there's no native compile step that would benefit
# from a separate builder stage.  Layer ordering puts deps before source
# so day-to-day source edits don't invalidate the dep cache.

# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PRISMA_HIDE_UPDATE_MESSAGE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Prisma Python's `prisma generate` shells out to the upstream Prisma TS
# CLI (it downloads a node env on first run), so the build needs node +
# npm available.  At runtime the generated Python client doesn't need
# node — just at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && npm --version

# Install uv from the official image (~3 MB, no apt-get noise).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests + the Prisma schema first.  This layer is
# cached as long as pyproject.toml / uv.lock / prisma/schema.prisma stay
# unchanged.  Source edits below don't trigger a fresh `uv sync`.
COPY pyproject.toml uv.lock README.md ./
COPY prisma/schema.prisma prisma/

# Install runtime dependencies (no dev extras, no editable install of the
# project itself yet).
RUN uv sync --frozen --no-dev --no-install-project

# Generate the Prisma client and download its query-engine binary into
# the venv.  Doing this at build time avoids a multi-second penalty on
# every cold start.
RUN uv run prisma generate

# Now copy the application source.  This is the layer that changes most
# often; everything above stays cached.
COPY src/ ./src/

# Install the project itself.  Fast because all deps are already cached.
RUN uv sync --frozen --no-dev

# Cloud Run injects PORT; default to 8080 for local docker run.
ENV PORT=8080
EXPOSE 8080

# Use `uv run` so the venv-managed uvicorn + Prisma client are picked up.
# --proxy-headers + --forwarded-allow-ips '*' makes FastAPI honour
# X-Forwarded-* headers that Cloud Run's load balancer injects (so
# request.url.scheme reports 'https' instead of 'http').
CMD ["sh", "-c", "uv run uvicorn src.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'"]
