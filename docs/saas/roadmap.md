# Roadmap

> **Audience:** customers, prospects, partners, internal contributors.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Last reviewed:** 2026-11-20. Dates below are historical targets unless reconfirmed by the owner; this roadmap is directional, not a release commitment.
> **Status:** Directional planning for product and fundraising discussions; not a contractual
> delivery commitment and not evidence that every listed item is staffed or funded.

This is what we plan to ship over the next four quarters, what we've shipped recently, and what we've decided not to do. The list is **directional**, not contractual — priorities shift in response to customer feedback, security needs, and ecosystem changes. Items committed in your contract take precedence over anything here.

## Recently shipped (last 90 days)

| Item | Shipped | Notes |
|---|---|---|
| Per-model LLM verification + auto-disable | 2026-04 | Closes the "retired model still in dropdown" hole; Verify probes the actual API, auto-disables on `unavailable` |
| Document upload + extraction (PDF / DOCX / MD / TXT) | 2026-04 | No persistence; in-flight extraction; routes on extension to handle Chrome's misreported content types |
| Branching node UI + branch label routing | 2026-04 | Two-handle UI for if-else / while / user-approval; `sourceHandle` → `branch` field on save |
| Transform `outputKey` ergonomics | 2026-04 | Compute and persist a named variable in one node — closes the "loop counter is verbose" gap |
| Vector DB upsert across all 5 providers | 2026-04 | Pinecone, Qdrant, Chroma, Weaviate, Milvus all do query + upsert; auto-chunking for string input |
| 21 reference templates | 2026-11-02 | Includes the decision-node routing example in addition to the earlier file-watch pipeline |
| Single + bulk delete on /runs/history | 2026-04-30 | Members delete their own; admin overrides (with the "admin can't delete other users' workflows" carve-out preserved) |
| MCP base64-blob sanitiser | 2026-05-04 | Closes the OAB-reported defect class; Highspot xlsx fetches no longer OOM the agent context |
| Attempt-health sweepers | 2026-11-19 | Expired leases and lease-less orphaned attempts are failed with compare-and-set guards; processing is not replayed and durable thread data remains available |
| Resilient detached-task wrapper for `/api/run/{slug}` | 2026-05-04 | Uncaught crashes from the async invocation path now persist failure before re-raising |
| World-class SaaS documentation overhaul | 2026-05-04 | This folder. Security / privacy / compliance / SLA / multi-tenancy / pricing / roadmap / support / customer onboarding + new operational runbooks |
| Workflow assignment & sharing | 2026-07-09 | Many-to-many sharing layered on top of single ownership; full read/write/run, no view-only split |
| Jira node | 2026-07-10 | Per-node Jira Cloud domain/email/API-token credentials; agentic loop over 6 Jira REST tools |
| Self-service "Forgot password?" flow | 2026-07-10 | Signed emailed reset link alongside the existing admin-forced reset; closes the account-enumeration vector |
| Designer autosave | 2026-07-10 | 3-second debounced autosave; fixes an owner's unsaved edits being invisible to a reassigned/shared user |
| File storage provider framework (`file-trigger` / `file-write`) + `composer watch` CLI | 2026-07-11 | Local folder-watch → production-workflow trigger; generated Markdown/Word/PDF file output |
| Approve-via-email + optional waiting-approval expiry | 2026-07-11 | Signed one-click approve/reject email links; expiry was later changed to opt-in, with the current default disabled so durable threads may wait subject to retention |
| Codex-audit security & correctness remediation (13 items) | 2026-07-12 | HTTP-node SSRF guard, boot-time production-config validation, scanner-safe email approvals, execution idempotency keys, and more — see the [Improvement Backlog](../claude-improvement-backlog.md) |
| Credential-disclosure gap closed for vector-DB / HTTP / MCP secrets | 2026-07-13 | Extends Jira's encrypt-at-rest + redact-on-read pattern to every secret-bearing node/server field |
| Durable execution via Cloud Tasks + Postgres-backed events/rate limits | 2026-07-15 | Runs, real-time events, and rate limits all now survive multi-instance deploys and Cloud Run scale-to-zero |
| Google Drive OAuth file-trigger (server-side polling) | 2026-07-16 | Cloud file-trigger with no local agent required, alongside the existing local `composer watch` |
| Confluence node | 2026-07-19 | Sibling to Jira: deterministic Confluence Cloud page create/update/get/set-property operations |
| Calendar pickers for Start-node `date`/`datetime` fields | 2026-07-20 | New Start-input field types render a calendar picker instead of a plain text box |

The full record is in [`../../CHANGELOG.md`](../../CHANGELOG.md).

## Candidate next investments

These are the highest-priority candidate investments for the next phase. Individual items may have
design work or prototypes, but sequencing depends on customer discovery, security priorities, and
available funding.

| Item | Why it matters | Status |
|---|---|---|
| **Vendor-neutral telemetry + immutable audit log + human-assisted recovery** | Application telemetry is currently host/log-provider dependent, execution history is not a complete audit ledger, and failed flows have no controlled checkpoint override/restart UI. These three controls should be designed together so every override is attributable and observable. | Design required; no committed date |
| **Postgres row-level security for shared-tenant prep** | Defence in depth before shared tenancy lands — RLS policies on every customer-data table, with `app.current_org_id` set per request. | Candidate; no committed date |
| **Customer data export endpoint** | A self-service export would replace custom SQL for GDPR/CCPA access requests. | Candidate; no committed date |
| **Bring-your-own-encryption-key (KMS-backed)** | Per-organisation KEK with KMS-issued data keys and a rotation runbook. | Candidate; no committed date |
| **Public status page (managed customers)** | Customer-visible uptime and incident history. | Candidate; no committed date |

## Longer-term roadmap

These have direction but the specifics are still moving.

| Item | Status |
|---|---|
| **Shared-tenant SaaS** — multiple customer organisations in one Composer instance. Schema and authz design is documented in [multi-tenancy.md](multi-tenancy.md). Gate is independently confirmed assurance requirements + RLS landing first. | Candidate; no committed date |
| **SOC 2 programme** — readiness work, independent assessment, Type I, observation period, and Type II are operator-level initiatives. | Not evidenced by this repository; no committed date |
| **Workflow versioning + rollback** — every save is a new version; published workflows pin to a specific version; rollback to a prior version is one click. | Candidate; no committed date |
| **Workflow staging + promotion** — add an explicit staging mode beyond today's draft/production flag. | Candidate; no committed date |
| **Native cron / scheduled triggers** — today external cron + `POST /api/run/{slug}` is the supported pattern. | Candidate; no committed date |
| **Self-serve organisation management** — depends on shared tenancy. | Candidate; no committed date |
| **Workflow templates marketplace (read-only)** — extend today's bundled templates with a community catalog. | Candidate; no committed date |
| **Cost attribution per workflow / per-user** — expose token spend by Composer surface. | Candidate; no committed date |
| **Workflow-level guardrails configuration** — deployment policy rather than only per-node configuration. | Candidate; no committed date |

## Speculative / under consideration

We've heard the request, we're thinking about it, no commitment yet:

- **HIPAA-eligible managed tier** — see [compliance.md](compliance.md) for the gap analysis. Demand-driven; if a customer needs it we'll scope it.
- **FedRAMP** — same as HIPAA: demand-driven, not yet on the funded path.
- **Self-hosted LLM model integration via vLLM / ollama** — Composer talks the OpenAI-compatible API surface today, so this works without code changes. Documenting the pattern as a first-class option is the actual ask.
- **Per-user LLM API keys** — the `llm_api_keys.userId` column is in the schema; the dispatcher would need to be taught to fall back through a per-user → per-org → per-deployment chain. Useful for centralised-but-attributable billing.
- **Mobile-friendly canvas** — the canvas is desktop-first; an "approve from your phone" view for human-in-the-loop nodes is the targeted phone use case, not full canvas editing.
- **Workflow analytics dashboard** — execution count, p50/p95 latency, success rate, top-failing nodes. We have the data; we don't have the UI yet.
- **Native test framework for workflows** — pytest-style assertions on workflow output. Today designers test in the Run Draft panel + manual review.
- **Inline diff between workflow versions** — depends on workflow versioning landing first.
- **Differential privacy / k-anonymity controls on outputs** — heard once from a regulated customer; we'd need a couple more asks to prioritise.

## What we've explicitly decided not to do

These are not on the roadmap. We've thought about them and chosen to focus elsewhere.

- **A cloud-native LLM orchestration platform from scratch.** LangGraph + LangChain are a strong foundation; reinventing them is not worth the cost.
- **Conversational chatbot UI as a first-class surface.** Composer is workflow-first. Customers who want chat build it as a workflow with an HTTP front-end; we won't ship a Slack-app-on-rails.
- **A workflow marketplace where third parties sell templates.** Open-source community contribution is on the speculative list; commercial marketplace economics are not where we want to spend product effort.
- **A "no-code" tier hiding the canvas behind a wizard.** The canvas is the abstraction we picked; making the wizard well is a separate product, not a Composer feature.
- **Replacing Postgres.** Postgres is the right choice. We've considered alternatives; none of them justify the migration.
- **Replacing LangGraph.** Same reasoning. The interrupt-resume + checkpoint primitives are the load-bearing piece; we don't have a reason to swap.

## How priorities get set

The roadmap balances four inputs:

1. **Customer commitments.** If we promised it in a contract, it wins.
2. **Security & compliance.** SOC 2 readiness, audit-log capability, and known security issues take priority over feature work.
3. **Strategic gates.** Shared tenancy unlocks new customer segments; the work to enable it (RLS, audit log, BYOK) gets weighted accordingly.
4. **Customer feedback volume + intensity.** Five customers asking for the same thing beats one customer asking for something different. We track requests in [GitHub Discussions](https://github.com/balajir2/composer/discussions).

Once a quarter we sit down with the priority order, look at what shipped, and update this document. Customers on Business + Enterprise plans get the spreadsheet behind it on request.

## Stay informed

- **Watching the [GitHub repo](https://github.com/balajir2/composer)** — every release tag updates the CHANGELOG
- **[GitHub Discussions](https://github.com/balajir2/composer/discussions)** — feature request voting + early-look design conversations
- **Customer success calls** (Business + Enterprise) — quarterly check-in with your CSM

If something on this list is critical to your decision to use Composer, raise it in your contract negotiation — we'd rather know about it than discover it three months in.

## Adjacent docs

- [`../../CHANGELOG.md`](../../CHANGELOG.md) — what's actually shipped
- [overview.md](overview.md) — what Composer is today
- [multi-tenancy.md](multi-tenancy.md) — the shared-tenant design we're building toward
- [compliance.md](compliance.md) — the certification roadmap detail
- [`../decisions.md`](../decisions.md) — ADRs that explain why some items aren't on the list
