# Documentation quality audit — 2026-11-20

## Outcome

Composer's active documentation was reviewed against the current repository and corrected where claims had drifted. This is a repository-accuracy audit, not a legal opinion, production-control attestation, penetration test, or independent compliance assessment.

## Scope and classification

- Starting inventory: 126 pre-existing documentation files under `docs/`, plus `README.md`, `CLAUDE.md`, and `frontend/README.md`. This audit added its plan and this report.
- Active/current-state material: repository READMEs, top-level product/engineering guides, `docs/operations/`, and `docs/saas/`.
- Historical material: `docs/archive/`, dated `docs/superpowers/specs/`, and dated `docs/superpowers/plans/`. These preserve the design or plan at that point in time and were checked for navigability, not rewritten to match later implementation.
- Point-in-time audits/backlogs: dated Claude/Codex audit files retain their original evidence. Status banners or inline updates now prevent old findings, versions, and scanner results from being mistaken for current state.

## Authoritative sources used

- Runtime/dependencies: `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/package-lock.json`, Dockerfiles.
- Product behavior: `src/`, frontend routes/components, `prisma/schema.prisma`, and migrations.
- CI/deployment: `.github/workflows/`, `scripts/gcp-bootstrap.ps1`, and deployment Dockerfiles.
- Reference content: `scripts/seed_templates.py`.
- Verification: repository link checker and a fresh pytest collection.

## Material corrections

1. Updated the frontend description from Next.js 14 to the installed Next.js 15.5.28. Historical plans/specifications still say Next.js 14 where that was their original baseline.
2. Updated the backend test count from 1,252 to 1,751, freshly collected during this audit.
3. Updated seeded reference-template counts from 19/20 to the 21 entries in `_TEMPLATES`.
4. Replaced the obsolete in-process execution-event description with the current Postgres event store, sequence cursor, and `LISTEN/NOTIFY` wake-up model.
5. Replaced the obsolete generic “stuck execution” description with current guarded lease-expiry and orphan-attempt failure semantics. The sweeper does not replay processing, and durable thread/checkpoint data is not the cancellation signal.
6. Replaced the generated create-next-app frontend README with Composer-specific development, verification, API-client, and deployment instructions.
7. Corrected observability claims: Composer does not currently ship OpenTelemetry, a Prometheus exporter, a universal structured-JSON logging formatter, or guaranteed request/user correlation fields on every log record.
8. Corrected audit claims: workflow executions, approvals, node results, and persisted execution events provide durable operational history, but Composer does not yet have a comprehensive immutable administrator audit ledger or self-service audit export.
9. Corrected privacy/schema claims: Composer's Prisma schema has no NextAuth `accounts` or `sessions` tables; the frontend uses JWT-based NextAuth sessions.
10. Qualified SaaS compliance, penetration-test, backup-frequency, DR-cadence, and certification statements that cannot be proven by source code. Operators must supply and verify those external commitments before customer publication.
11. Refreshed the roadmap so expired quarter targets are not presented as active commitments, and grouped telemetry, audit, and human-assisted recovery as one design initiative.
12. Clarified that centralized encrypted LLM keys are an intentional enterprise capability. They remain deployment-scoped by design; this audit does not reclassify central key storage as a defect. Per-user/BYOK alternatives remain separate product choices.

## Verification

- `python scripts/check_doc_links.py`: all relative links resolve after edits.
- `pytest --collect-only -q`: 1,751 tests collected.
- Targeted stale-claim scans covered framework versions, test/template counts, event transport, sweepers, structured logging, telemetry, audit-log language, and historical roadmap dates.
- The final diff was reviewed to keep historical plans/designs intact and avoid changing application behavior.

## Residual owner-controlled facts

The repository cannot validate whether a particular operator has completed a SOC 2 assessment, penetration test, legal review, backup schedule, disaster-recovery exercise, uptime measurement, support staffing commitment, or signed subprocessor contract. Documents in `docs/saas/legal/` are templates and must be reviewed by qualified counsel and the deployment operator before use. Placeholder tokens in those templates are intentional publication-time inputs, not code defects.

## Maintenance rule

When code changes a public route, schema, dependency major version, template inventory, deployment topology, execution lifecycle, security control, or operator commitment, update the corresponding active guide in the same change. Do not rewrite historical plans or archived design records; add a dated supersession note instead.
