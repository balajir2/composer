# Product Capabilities

This document is the feature catalogue for Composer. It describes user-visible capabilities and
points to the implementation or deeper documentation. The code and API schemas remain the final
source of truth when documentation and implementation disagree.

## Product surfaces

| Surface | Capabilities |
|---|---|
| Designer | Canvas authoring, node configuration, variables, autosave, draft execution, templates, publishing, workflow sharing. |
| Runs | Input collection, live node progress, execution results, approval actions, history, API-key management. |
| Admin | Users, roles, LLM keys, model catalogue and verification, MCP servers and OAuth, deployment settings, global execution visibility. |
| API | Workflow CRUD, execution lifecycle, uploads, approvals, WebSocket events, production external invocation. |

## Workflow nodes

Composer currently exposes 24 node types in the Designer palette.

| Category | Node | Function |
|---|---|---|
| Boundary | Start | Declares typed workflow inputs and applies defaults/required validation. |
| Boundary | End | Terminates a path and surfaces the latest workflow output. |
| Canvas | Note | Adds non-executable documentation to a workflow. |
| AI | Agent | Runs an LLM with prompts, selected tools, MCP access, and optional structured output. |
| AI | Extract | Converts text into structured output with an LLM. |
| Integration | MCP | Invokes tools exposed by a configured Model Context Protocol server. |
| Integration | HTTP | Sends a direct HTTP request with variable-substituted URL, headers, and body. |
| Integration | Jira | Uses an LLM-driven Jira toolset to create, search, read, update, transition, and comment on issues. |
| Integration | Confluence | Deterministic (no LLM) Confluence Cloud page operations — create/update a page, get a page, get/set a page content-property. |
| Integration | Email | Sends text or HTML email through Resend. |
| Integration | Arcade | Invokes an Arcade tool with per-user authorization when configured. |
| Integration | Gamma AI | Generates presentations, documents, or social content through Gamma. |
| Integration | File Trigger | Visual-only folder-watch configuration — local folder via the `composer watch` CLI, or a Google Drive folder via server-side polling — to trigger production runs. |
| Integration | File Write | Writes generated content to a local file (Markdown, DOCX, PDF, or HTML) via a storage provider. |
| Integration | Download PDF | Renders HTML or Markdown content to a PDF and writes it to a local folder or a connected Google Drive folder. |
| Data | Vector DB | Queries or upserts content across five supported vector databases. |
| Data | Set State | Writes a value into workflow state. |
| Data | Transform | Evaluates a sandboxed expression and optionally names the result. |
| Data | Data Transform | Maps, filters, or reduces a collection with sandboxed expressions. |
| Data | Join Chunks | Combines string/document chunks with configurable formatting. |
| Control | If / Else | Routes execution using a sandboxed condition. |
| Control | While | Repeats a bounded body branch until its condition is false. |
| Control | User Approval | Checkpoints execution, waits for approve/reject, and routes by decision. |
| Safety | Guardrails | Runs configurable PII, moderation, jailbreak, and hallucination checks. |

Detailed configuration examples live in the [Designer Guide](designer-guide.md).

## LLM and model management

- Anthropic, OpenAI, Google, and Groq provider integrations.
- Additional OpenAI-compatible model routing represented in the admin model catalogue.
- Centralized encrypted LLM keys.
- Admin model discovery, verification, enable/disable controls, and designer model selection.
- Structured JSON output and schema-aware downstream variable access.
- Optional LangSmith tracing.

## Tools and enterprise integrations

- Built-in providers for Tavily, Firecrawl, Serper, Browserless, Gamma, and Arcade.
- Jira Cloud REST API v3 toolset and a paired, deterministic Confluence Cloud page toolset — both use per-node, per-workflow credentials (domain/email/API token), encrypted at rest and redacted on every read.
- Model Context Protocol servers with no auth, static auth, bearer/API-key auth, or OAuth.
- Per-user MCP OAuth tokens and administrator-controlled shared servers.
- MCP tool discovery and response sanitisation for oversized embedded binary data.
- Direct HTTP calls for APIs without a dedicated node/provider, guarded against SSRF (IP-literal, DNS-resolution, and cloud-metadata-hostname blocking, plus a response-size cap and credential-redacting error messages).
- Google Drive OAuth connections (per-user, encrypted tokens) backing the file-trigger, file-write, and download-pdf nodes' Drive support.

## Data and documents

- In-memory extraction from PDF, DOCX, Markdown, and plain text uploads.
- Pinecone, Qdrant, Chroma, Weaviate, and Milvus query/upsert support.
- Configurable embedding provider/model support.
- Automatic character-window chunking for string ingestion.
- Metadata filters, namespaces, score thresholds, and optional result joining.
- Folder-watch triggers and generated-file delivery (Markdown/DOCX/PDF/HTML) across a pluggable local-filesystem or Google Drive storage provider.

## Workflow control and human oversight

- Explicit branching and bounded loops.
- PostgreSQL-backed LangGraph checkpoints.
- In-application and emailed approval paths, with optional file attachments on the approval email.
- Approval audit records and expiry sweeper.
- Named variables, node aliases, and structured output references.
- Draft execution and published production endpoints.
- Idempotency keys and execution cancellation for safe client retries.

## Authentication and authorization

- Standalone email/password authentication.
- Azure AD SSO through the frontend authentication layer.
- Embedded deployment JWT mode.
- Admin/member roles.
- Owner and assignment-based workflow access, with self-service sharing via `GET /users/search`.
- Per-user API keys for external invocation.
- Admin-forced and self-service password reset, forced password change, and user activation controls.

## Runtime and observability

- Node-started, node-completed, node-failed, approval-required, and workflow-completed events.
- WebSocket live execution feed with a durable, sequence-numbered event log and reconnect cursor — no missed events across a dropped connection or backend instance.
- Durable, queued execution via Cloud Tasks with lease/heartbeat recovery, surviving Cloud Run scale-to-zero mid-run.
- Execution inputs, outputs, variables, node results, timing, and error persistence.
- LangSmith support for LLM traces.
- Structured backend logging and request correlation.
- Stuck-run, expired-approval, expired-lease, and event-retention maintenance sweepers.

## Developer and operator experience

- FastAPI-generated OpenAPI schema and generated TypeScript API types.
- Strict Pyright, Ruff, pytest, Vitest, and Playwright tooling.
- Docker, Vercel, GCP Cloud Run, Postgres, SSO, monitoring, incident, scaling, and disaster-recovery guidance.
- ADR history explaining material architectural decisions.
- Twenty seeded workflow templates covering common patterns.

## Current boundaries

For technical diligence, distinguish shipped breadth from production hardening still in progress:

- Durable queued execution (Cloud Tasks), cross-instance execution events, and Postgres-backed
  rate limits now support Cloud Run's multi-instance autoscaling correctly — see ADR-0033.
- A single canonical credential/connection model (rotation, ownership, cross-workflow visibility)
  remains deferred. Jira, Confluence, vector-DB, HTTP-header, and MCP secrets are each encrypted
  at rest and redacted on read today, but via per-field patterns rather than one shared
  abstraction.
- Shared-tenant organization isolation is roadmap work, not a shipped claim.
- SOC 2 certification is not currently held.
- Commercial pricing and service commitments are proposals until contracted.

See [Improvement Backlog](claude-improvement-backlog.md), [Roadmap](saas/roadmap.md), and
[Compliance](saas/compliance.md) for the detailed status.
