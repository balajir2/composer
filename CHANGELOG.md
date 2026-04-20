# Changelog

All notable changes to Composer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Phase 0 — Scaffolding (2026-04-20)

#### Added
- Initial repo scaffold: FastAPI skeleton, Prisma schema placeholder, docker-compose
- `pyproject.toml` with full dependency list (FastAPI, LangGraph, LangChain providers, Prisma, pytest, ruff, pyright)
- `/health` endpoint returns service metadata
- CI workflow (lint + typecheck + tests)
- `.env.example` supporting both Neon (primary) and local Docker Postgres (fallback)
- MIT License
- README with setup instructions
