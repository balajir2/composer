# Composer backend (FastAPI + LangGraph + Prisma) — Cloud Run image.
#
# Build:   docker build -t composer-backend .
# Run:     docker run -e DATABASE_URL=... -e JWT_SECRET=... -p 8080:8080 composer-backend
#
# Multi-stage build:
#   builder  - Python 3.12 + Node.js + uv.  Installs deps, generates
#              the Prisma client + downloads the query-engine binary.
#   runtime  - Python 3.12 only.  Copies /app from builder (the venv,
#              the source, and the Prisma cache).  No Node.js, no uv,
#              no apt-cache.  Final image ~400 MB instead of ~1.3 GB.
#
# Node.js is needed by Prisma Python's `generate` (it shells out to the
# upstream Prisma TS CLI to download the query engine).  The runtime
# image needs the resulting engine BINARY but not the Node toolchain
# that fetched it.

# syntax=docker/dockerfile:1.7

# ─── builder ──────────────────────────────────────────────────────────
# Base images are pinned by multi-arch index digest (captured 2026-11-02 via
# `docker buildx imagetools inspect`).  Bump the tag AND digest together,
# deliberately; never re-point to a floating tag.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PRISMA_HIDE_UPDATE_MESSAGE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    # Put the Prisma binary cache inside /app so the runtime copy picks
    # it up.  Default ~/.cache/prisma-python wouldn't travel across
    # stages.
    PRISMA_BINARY_CACHE_DIR=/app/.prisma-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv from the official image (~3 MB), pinned to 0.8.15 (matches the local
# toolchain) by index digest, captured 2026-11-02.
COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:a5727064a0de127bdb7c9d3c1383f3a9ac307d9f2d8a391edc7896c54289ced0 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency manifests + Prisma schema first so this layer caches
# independently of source changes.
COPY pyproject.toml uv.lock README.md ./
COPY prisma/schema.prisma prisma/

RUN uv sync --frozen --no-dev --no-install-project

# Download the Prisma query-engine binary + generate the Python client
# at BUILD time so runtime cold starts don't pay the multi-second
# download penalty.  The binary lands in $PRISMA_BINARY_CACHE_DIR.
RUN uv run prisma generate

COPY src/ ./src/

# Install the project itself (fast — deps are cached).
RUN uv sync --frozen --no-dev

# ─── runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRISMA_HIDE_UPDATE_MESSAGE=1 \
    PRISMA_BINARY_CACHE_DIR=/app/.prisma-cache \
    # Put the venv's bin/ first so `uvicorn` and friends resolve
    # without needing `uv run`.
    PATH=/app/.venv/bin:$PATH \
    PORT=8080

WORKDIR /app

# Copy everything we need from the builder in a single layer.  This
# brings the venv (with the generated Prisma client), the source, the
# project manifest, and the Prisma engine cache — nothing else.  No
# Node.js binary, no apt-cache, no uv, no nodeenv install.
COPY --from=builder /app /app

# Run as an unprivileged user (fixed uid/gid 10001, no login shell).  /app
# stays root-owned and world-readable/executable (code + venv + Prisma
# engine are NOT writable by the app).  Only the paths the app writes at
# runtime are owned by the app user:
#   /tmp/composer-attachments  default APPROVAL_ATTACHMENT_ROOT
#   /home/app                  HOME, for any library that wants ~/.cache
RUN groupadd --system --gid 10001 app     && useradd --system --uid 10001 --gid 10001 --home-dir /home/app         --shell /usr/sbin/nologin app     && mkdir -p /home/app /tmp/composer-attachments     && chown 10001:10001 /home/app /tmp/composer-attachments     && chmod -R a+rX /app

ENV HOME=/home/app

USER 10001:10001

EXPOSE 8080

# uvicorn's own proxy-header handling is OFF on purpose. With
# `--forwarded-allow-ips '*'` it takes the LEFTMOST X-Forwarded-For entry,
# which is whatever the client sent — letting callers spoof the address the
# login / approval-email rate limiters see (audit P1-6). The app instead
# installs src/security/proxy_headers.py's TrustedProxyHeadersMiddleware,
# which trusts only the proxy-appended entry from the right
# (TRUSTED_PROXY_HOPS, default 1 = Cloud Run's front end) and also restores
# the https scheme from X-Forwarded-Proto.
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers"]
