# Composer frontend (Next.js 14) — Cloud Run image.
#
# Build:   docker build -t composer-frontend \
#            --build-arg NEXT_PUBLIC_COMPOSER_API_URL=https://api.example.com .
# Run:     docker run -e NEXTAUTH_SECRET=... -p 8080:8080 composer-frontend
#
# Multi-stage build:
#   deps      → install node_modules with full dev deps for the build
#   builder   → run next build with the public API URL baked in
#   runner    → minimal node:20-alpine + the standalone server output
# Final image is ~150 MB.  Requires next.config.mjs to set
# `output: "standalone"`.

# syntax=docker/dockerfile:1.7

# ─── deps ───────────────────────────────────────────────────────────
# Base image pinned by multi-arch index digest (captured 2026-11-02 via
# `docker buildx imagetools inspect`).  Bump tag AND digest together,
# deliberately.
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS deps
WORKDIR /app

# Copy lockfile + manifest first so this layer caches independently of
# source changes.
COPY package.json package-lock.json* ./

# Use npm ci for deterministic, lockfile-respecting installs.
RUN npm ci

# ─── builder ────────────────────────────────────────────────────────
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS builder
WORKDIR /app

# Bring in the cached node_modules.
COPY --from=deps /app/node_modules ./node_modules

# Copy the rest of the frontend source (filtered by .dockerignore).
COPY . .

# Next.js bakes NEXT_PUBLIC_* env vars at build time.  Pass the backend
# URL as a build-arg; the deploy workflow + bootstrap script set this to
# the production API hostname.
ARG NEXT_PUBLIC_COMPOSER_API_URL
ENV NEXT_PUBLIC_COMPOSER_API_URL=${NEXT_PUBLIC_COMPOSER_API_URL}

# Google Picker API key for the Drive folder-picker embed (Google Drive
# OAuth file-trigger, 2026-07-16) — restricted server-side to the Picker
# API + this frontend's own origin, so embedding it here is safe by
# design (see docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §F).
ARG NEXT_PUBLIC_GOOGLE_PICKER_API_KEY
ENV NEXT_PUBLIC_GOOGLE_PICKER_API_KEY=${NEXT_PUBLIC_GOOGLE_PICKER_API_KEY}

# Disable Next.js telemetry in builds (no phone-home from our images).
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ─── runner ─────────────────────────────────────────────────────────
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS runner
WORKDIR /app
# The base image is pinned by digest (reproducible), which also freezes its OS
# packages; patch them at build time so known-fixed CVEs (e.g. OpenSSL) don't ride
# along until the next deliberate digest bump. CI's Trivy scan enforces this.
RUN apk upgrade --no-cache

# The runtime only runs `node server.js`. npm/npx/corepack/yarn are not needed and
# ship their own vendored dependencies (tar, glob, minimatch, ...) that scanners
# flag; removing them shrinks the image and the attack surface.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    HOSTNAME="0.0.0.0"

# Run as non-root.  Next.js's standalone output runs cleanly as a
# non-privileged user.
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy the standalone server output + the static asset directory.
# `public/` is included for any static assets shipped alongside.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 8080

# The standalone build emits server.js at the root.
CMD ["node", "server.js"]
