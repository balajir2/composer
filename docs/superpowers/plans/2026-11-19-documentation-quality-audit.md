# Documentation Quality Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Composer's active documentation accurate, internally consistent, navigable, and traceable to the current code and deployment configuration.

**Architecture:** Treat source code, package manifests, Prisma schema, routes, CI workflows, and deployment configuration as authoritative. Audit active user, engineering, operations, and SaaS documents against those sources; preserve archived designs and dated plans as historical records, while ensuring indexes label them correctly.

**Tech Stack:** Markdown, PowerShell, Python/pytest collection, Next.js package metadata, FastAPI/OpenAPI routes, GitHub Actions YAML, Prisma.

**Spec:** User request in the 2026-11-19 conversation: quality-audit all documents and make them current and accurate.

## Global Constraints

- Preserve unrelated user files and existing uncommitted work.
- Do not rewrite archived designs, dated specifications, or completed implementation plans to describe current behavior.
- Validate factual claims against repository sources rather than against other prose documents.
- Do not invent production, legal, compliance, pricing, or SLA commitments that cannot be verified in the repository.
- Keep customer-facing legal documents explicitly identified as templates requiring counsel review.

---

### Task 1: Inventory and classification

**Files:**
- Modify: `docs/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: repository Markdown inventory and current directory structure.
- Produces: an explicit active-versus-historical documentation map used by the rest of the audit.

- [ ] **Step 1:** Inventory Markdown and classify active guides, operational runbooks, SaaS templates, dated plans/specifications, and archive records.
- [ ] **Step 2:** Compare index links with files on disk and fix missing or misleading navigation.
- [ ] **Step 3:** Verify all relative Markdown links resolve.

### Task 2: Technical truth audit

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `frontend/README.md`
- Modify: active Markdown files directly under `docs/`
- Modify: `docs/operations/*.md`

**Interfaces:**
- Consumes: `pyproject.toml`, `uv.lock`, `frontend/package.json`, `prisma/schema.prisma`, `src/main.py`, API routers, executors, migrations, scripts, Dockerfiles, and `.github/workflows/*.yml`.
- Produces: technically accurate setup, architecture, API, operations, and contributor guidance.

- [ ] **Step 1:** Check runtime/framework versions, supported Python/Postgres versions, commands, paths, ports, and deployment topology.
- [ ] **Step 2:** Check API paths, statuses, node types, data model claims, execution lifecycle, cancellation/resume semantics, telemetry, audit, and recovery guidance.
- [ ] **Step 3:** Check CI/test/security statements against workflows and fresh repository counts.
- [ ] **Step 4:** Correct every verified mismatch with narrow edits.

### Task 3: Product and SaaS claim audit

**Files:**
- Modify: `docs/product-capabilities.md`
- Modify: `docs/overview.md`
- Modify: `docs/designer-guide.md`
- Modify: `docs/admin-guide.md`
- Modify: `docs/saas/*.md`
- Modify: `docs/saas/legal/*.md`

**Interfaces:**
- Consumes: implemented UI routes/components, backend routes, schema, security controls, templates, and deployment behavior.
- Produces: current product claims and clearly qualified commercial/legal templates.

- [ ] **Step 1:** Separate implemented capabilities from roadmap or planned commitments.
- [ ] **Step 2:** Reconcile template counts, supported integrations, roles, tenancy, retention, backup, observability, SLA, pricing, and compliance language with evidence.
- [ ] **Step 3:** Add or retain explicit qualification where a claim depends on operator configuration, external contracts, or legal review.

### Task 4: Verification and audit record

**Files:**
- Create: `docs/documentation-audit-2026-11-20.md`

**Interfaces:**
- Consumes: all corrected active documentation and verification outputs.
- Produces: a dated audit record listing scope, authoritative sources, corrections, exceptions, and remaining owner decisions.

- [ ] **Step 1:** Run the repository link checker or an equivalent local relative-link validator.
- [ ] **Step 2:** Scan for stale versions, counts, obsolete paths, placeholder markers, and contradictory current-state claims.
- [ ] **Step 3:** Review the final diff for unsupported legal/commercial changes and unrelated churn.
- [ ] **Step 4:** Record results and any residual items that require product, legal, or operational owner confirmation.
