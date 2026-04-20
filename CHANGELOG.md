# Changelog

All notable changes to Composer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Phase 1 — Execution engine core (2026-04-20, in progress)

#### Added
- [Phase 1 design spec](docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md) — architecture for the execution engine, Prisma schema additions, Pydantic models for all 18 node types, checkpointer design, API surface, test plan, phase-exit checklist.
- [Decisions log](docs/design/decisions.md) established with ADR-0001 through ADR-0005:
  - ADR-0001: LangGraph checkpointer — Prisma owns schema, thin custom saver.
  - ADR-0002: Workflow JSON schema — full OAB fidelity (all 18 node types modeled in Phase 1).
  - ADR-0003: Documentation runs in lockstep with development (four-document system + enforcement).
  - ADR-0004: Local dev Postgres — Neon only (supersedes Phase 0's dual-path guidance).
  - ADR-0005: Phase 1 API surface (3 endpoints) + JWT primitives as library code only.

#### Changed
- README points at `docs/design/` and `docs/superpowers/specs/` for design/spec/ADR docs.
- README stack table: corrected the real-time row to SSE (Phase 5) → WebSocket (Phase 9), matching the phased plan.
- `.env.example`: Neon-only `DATABASE_URL` format; added `TEST_DATABASE_URL` for integration tests.

#### Removed
- `docker-compose.yml` (per ADR-0004). Local dev is Neon-only; integration tests in CI use a GitHub Actions Postgres service container.
- README "Offline fallback: local Postgres" section.

### Phase 0 — Scaffolding (2026-04-20)

#### Added
- Initial repo scaffold: FastAPI skeleton, Prisma schema placeholder.
- `pyproject.toml` with full dependency list (FastAPI, LangGraph, LangChain providers, Prisma, pytest, ruff, pyright).
- `/health` endpoint returns service metadata.
- CI workflow (lint + typecheck + tests).
- `.env.example` template.
- MIT License.
- README with setup instructions.

> Historical note: Phase 0 originally shipped a `docker-compose.yml` for local Postgres fallback. ADR-0004 (Phase 1) supersedes that decision; Neon is the sole dev path going forward.
