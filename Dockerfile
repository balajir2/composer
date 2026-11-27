# Composer backend (FastAPI + LangGraph + SQLAlchemy) — Cloud Run image.
#
# Build:   docker build -t composer-backend .
# Run:     docker run -e DATABASE_URL=... -e JWT_SECRET=... -p 8080:8080 composer-backend
#
# Multi-stage build:
#   builder  - Python 3.12 + uv. Installs the locked dependencies and the project into /app/.venv.
#   runtime  - Python 3.12 only. Copies /app from builder (the venv and the source). No uv,
#              no apt cache, no build tools.
#
# Migrations are not run from the image: the deploy workflow runs `alembic upgrade head`
# from its own checkout before the new revision takes traffic.

# syntax=docker/dockerfile:1.7

# ─── builder ──────────────────────────────────────────────────────────
# Base images are pinned by multi-arch index digest (captured 2026-11-19 via
# `docker buildx imagetools inspect`).  Bump the tag AND digest together,
# deliberately; never re-point to a floating tag.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv from the official image (~3 MB), pinned to 0.8.15 (matches the local
# toolchain) by index digest, captured 2026-11-02.
COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:a5727064a0de127bdb7c9d3c1383f3a9ac307d9f2d8a391edc7896c54289ced0 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency manifests first so this layer caches independently of source changes.
COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/

# Install the project itself (fast — deps are cached).
RUN uv sync --frozen --no-dev

# ─── runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Put the venv's bin/ first so `uvicorn` and friends resolve
    # without needing `uv run`.
    PATH=/app/.venv/bin:$PATH \
    PORT=8080

WORKDIR /app

# Pull fixed Debian security packages that may be newer than the pinned base
# image rebuild (for example apt 3.0.4, zlib deb13u1 and libzstd1 deb13u1).
# CI and deploy builds use a layer cache keyed on this instruction's text, so
# without a changing input this upgrade layer is reused forever and new
# security fixes never land. Bump SECURITY_REFRESH whenever the image scan
# reports a fixed OS package (last bumped for CVE-2026-124851, libzstd1).
ARG SECURITY_REFRESH=2026-11-25
RUN echo "security refresh ${SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy everything we need from the builder in a single layer.  This
# brings the venv, the source and the project manifest — nothing else.
COPY --from=builder /app /app

# Run as an unprivileged user (fixed uid/gid 10001, no login shell).  /app
# stays root-owned and world-readable/executable (code + venv
# are NOT writable by the app).  Only the paths the app writes at
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
