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
FROM node:20-alpine AS deps
WORKDIR /app

# Copy lockfile + manifest first so this layer caches independently of
# source changes.
COPY package.json package-lock.json* ./

# Use npm ci for deterministic, lockfile-respecting installs.
RUN npm ci

# ─── builder ────────────────────────────────────────────────────────
FROM node:20-alpine AS builder
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

# Disable Next.js telemetry in builds (no phone-home from our images).
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ─── runner ─────────────────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app

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
