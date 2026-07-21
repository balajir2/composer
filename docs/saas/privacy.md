# Privacy & Data Handling

> **Audience:** customers, data protection officers, end users.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **For a customer-ready notice intended to be served at signup:** see the privacy policy template at [legal/privacy-policy.md](legal/privacy-policy.md). This document is the operational truth that policy is built on.

## What data Composer holds about a customer organisation

Composer stores three categories of data:

1. **Identity & access data** — who logs in, what they're allowed to do.
2. **Workflow & execution data** — what they build and run.
3. **Operational telemetry** — logs and traces that prove the platform works.

The full schema lives in [`../../prisma/schema.prisma`](../../prisma/schema.prisma); the table below is the digest with privacy-relevant context.

### Identity & access

| Table | What it stores | PII? | Lawful basis (GDPR) |
|---|---|---|---|
| `users` | Email, name (if SSO supplies it), bcrypt-hashed password (standalone only), role, `isActive` flag, timestamps | Yes — email, name | Contract / legitimate interest |
| `accounts` (NextAuth) | OAuth provider linkages | Possibly — provider userId | Contract |
| `sessions` (NextAuth) | Active session tokens | No (session id only) | Contract |
| `api_keys` | bcrypt-hashed key, label, prefix, owner, last-used timestamp, expiry | No (label may include PII if user includes their email) | Contract |
| `mcp_servers` + `mcp_oauth_tokens` | Encrypted OAuth tokens for the MCP server, scopes, issuer | No directly; tokens grant access to *external* systems whose data may include PII | Contract |
| `cloud_storage_connections` | Encrypted per-user OAuth tokens for cloud file-trigger providers (Google Drive today) | No directly; tokens grant access to the connected Drive folder's contents | Contract |
| `llm_api_keys` | Encrypted provider API keys | No | Contract |

### Workflow & execution

| Table | What it stores | PII? |
|---|---|---|
| `workflows` | Full node + edge graph, name, description, tags, `isPublic`/`isProduction` flags, owner, slug, timestamps | Possibly — workflows can encode prompts, examples, and references. Treat as confidential by default. |
| `workflow_executions` | Input, output, variables, node results, status, error, timestamps, owning user | Possibly — execution input is whatever the caller submits |
| `langgraph_checkpoints`, `checkpoint_writes` | Per-step state snapshots used by LangGraph for resumability | Possibly — same content as execution input/output |
| `approvals` | Human-approval decisions, the deciding user, optional feedback string | Possibly — feedback may include PII |
| Document upload (`POST /uploads/extract-text`) | **Not stored** — extracted text is returned to the caller; the request stream is closed and bytes released | n/a |

### Operational telemetry

| Source | What's captured | Where it lives |
|---|---|---|
| Backend logs | HTTP method, path, status code, calling user id, request id, timing, error stacks | Container host (Fly logs, CloudWatch, etc.); retention per host configuration |
| LangSmith (optional) | Full prompt + tool-call trace per execution run | LangSmith SaaS; opt-in via `LANGCHAIN_TRACING_V2=true` |
| Vercel analytics (frontend, optional) | Page views, web-vital metrics | Vercel; opt-in via Vercel project setting |

If you don't enable LangSmith or Vercel analytics, the only telemetry that leaves your deployment is what you choose to ship to your own log aggregator.

## Data we deliberately don't collect

- **No third-party trackers in the frontend.** No Google Analytics, no Segment, no Pendo, no Hotjar. The frontend ships with no analytics scripts.
- **No automatic prompt or response collection beyond the database row that proves the run happened.** LangSmith is opt-in; it routes the same prompts/responses to LangSmith's storage when on.
- **No sale or sharing of customer data.** We never sell data and never expose one customer's data to another customer's workflows.
- **No phone-home telemetry from the codebase.** The only outbound calls Composer makes are: (a) what your workflows tell it to make, (b) LLM provider calls, (c) MCP server calls, (d) optional LangSmith trace shipping.

## Document uploads — explicit non-persistence guarantee

The document-upload feature (`POST /uploads/extract-text`) is the most privacy-sensitive code path because customers upload contracts, transcripts, and policy docs through it. The contract:

> Composer reads the upload stream into memory, extracts plain text via pypdf / python-docx, returns the text to the caller, and releases all buffers. **Nothing is written to disk. Nothing is written to Postgres. Nothing is shipped to LangSmith or any other observability sink** beyond the standard request-completion log line ("upload extracted, N bytes, M chars, took T ms").

If you need persistent storage for uploaded documents — for re-running, audit trail, or cross-execution reference — the recommended pattern is to upload to your own object storage and have your workflow read from there via an HTTP node.

## Retention

Composer doesn't enforce retention from the application — it persists everything for as long as Postgres holds it. Retention is a deployment-policy concern. The table below is the retention model we recommend; managed customers can request a custom policy in the contract.

| Data | Recommended retention | How to delete |
|---|---|---|
| User accounts | Until off-boarding | Admin → Users → Deactivate (soft delete); `DELETE FROM users` for hard delete after legal hold expires |
| Per-user API keys | Until revoked or expired | Runs → API keys → Revoke (immediate); rows kept for the audit trail |
| Workflows | Indefinite — these are the customer's product | Owner deletes via UI; admin can reassign before deletion |
| Execution rows + checkpoints | 90 days for runs of published workflows; 30 days for draft runs (recommended) | Manual via the runs page; bulk-delete + delete-all are admin-accessible. Periodic Postgres-level pruning on `started_at` is reasonable. |
| Approval records | Same as the parent execution | Cascaded by FK |
| Backend logs | 30–90 days at the host level | Configure on the container host |
| LangSmith traces | Per LangSmith plan (typically 14–365 days) | Configure in LangSmith |

The defaults above match what we'd recommend writing into a customer's data-retention policy. Adjust per your regulatory regime — HIPAA, financial services, etc.

## Deletion paths

A customer is entitled to have their data deleted within a reasonable window. The mechanics:

| Customer asks for | Operation |
|---|---|
| Delete one execution | UI: Runs → row → Delete; cascades to checkpoints + approvals |
| Delete a batch of executions | UI: Runs → multi-select → Delete; or `POST /executions/delete-bulk` |
| Delete every execution on the deployment | Admin "Delete all" button (admin-only); typically used for staging cleanup |
| Delete a workflow | Owner UI: workflow → Delete; cascades to executions + checkpoints |
| Deactivate a user | Admin → Users → toggle `isActive=false`; user can no longer sign in, their data stays |
| Hard-delete a user (right to erasure) | SQL: `DELETE FROM users WHERE id = $1` after confirming all `WorkflowExecution` rows owned by the user are also gone (FK constraints will prevent orphaning); see [`../operations/admin-operations.md`](../operations/admin-operations.md) |
| Export everything we know about a user (right to portability) | `GET /admin/users/{id}/export` returning JSON of every `users` row + their `workflows` + their `workflow_executions` + their `api_keys` (hashes only) — **on the [roadmap](roadmap.md); not yet built**. Today it's a custom SQL export. |

For managed customers, the SLA on a verified deletion request is **30 days from acknowledgement** (operationally we aim for same-week). Backups containing the data may persist past the deletion until they're rotated out per backup retention.

## Sub-processors

Composer routes data to a small set of third parties, each of which has its own data-handling commitments. Customers operating under GDPR / CCPA must record this list and may need to flow it through to their own customers.

| Sub-processor | What we send them | Why | Data residency |
|---|---|---|---|
| **Anthropic** (Claude API) | The prompt + tool definitions for each agent invocation, when the workflow uses an Anthropic model | LLM inference | US (default) — see Anthropic's privacy statement |
| **OpenAI** | Same as above for OpenAI models; also the embedding text for vector-DB upserts using `text-embedding-3-small` | LLM inference + embeddings | US (default) |
| **Google AI Studio** | Same as above for Gemini models | LLM inference | Per Google's data location policy |
| **Groq** | Same as above for Groq-hosted models | LLM inference | US |
| **Tavily, Firecrawl, Serper, Browserless** | The query / URL the workflow author specified | Web search + scraping | Per provider |
| **Gamma** | Outline content the workflow generated | Slide deck rendering | US |
| **Arcade** | Tool-call payloads for the workflow's enabled Arcade tools | Tool execution | US |
| **Atlassian (Jira Cloud / Confluence Cloud)** | Issue/page content and the per-node domain/email/API-token credential the designer configures on the `jira` or `confluence` node | Ticket + wiki-page automation | Per Atlassian Cloud site |
| **Google Drive** (optional, per `file-trigger` node) | OAuth-scoped read/write access to the folder(s) a designer connects, for server-side file-watch polling | Cloud file-trigger ingestion | Per Google's data location policy |
| **MCP servers** (per workflow) | Whatever the workflow's MCP nodes call them with | External integrations | Per server |
| **LangSmith** (optional) | Full execution trace including prompts + tool calls + outputs | Tracing + debugging | US — opt-out by leaving `LANGCHAIN_TRACING_V2=false` |
| **Pinecone / Qdrant / Chroma / Weaviate / Milvus** (optional) | Vector embeddings + metadata of customer documents | RAG retrieval | Per provider |
| **Neon** (recommended Postgres host) | Everything in the database | Hosting | Per region selected at provisioning |
| **Vercel** (recommended frontend host) | Customer-uploaded data only via the frontend's API proxy | Hosting | Per region |

A static, versioned list intended for legal review is at [legal/subprocessors.md](legal/subprocessors.md). Update it whenever a new provider is added, and notify managed customers per the contract.

## Data residency

The hosting region is your choice when we provision a managed-single-tenant deployment. Today we offer:

- **EU**: Neon EU + Vercel `fra1` / `cdg1` / `arn1`
- **US**: Neon US + Vercel `iad1` / `sfo1` / `pdx1`
- **Asia-Pacific**: Neon AP + Vercel `hnd1` / `sin1`

LLM provider calls leave the region by default (Anthropic / OpenAI / Google host primarily in the US). For customers with strict residency requirements, options:

1. Use only providers with the residency you need (e.g. Anthropic's data residency offering).
2. Disable cloud providers entirely and run a self-hosted model on the same VPC. Composer talks OpenAI-compatible APIs, so anything with that surface (vLLM, ollama with the OpenAI shim, Groq's local-tier offering) works.
3. Self-host Composer in your own VPC. The runbooks are in [`../operations/`](../operations/).

## Customer rights mapping (GDPR / CCPA)

| Right | How customers exercise it |
|---|---|
| **Right of access** | `GET /admin/users/{id}/export` (roadmap) or admin-issued SQL export |
| **Right to rectification** | Admin → Users → edit fields; or self-serve via account settings (in flight) |
| **Right to erasure** | Hard-delete path under "Deletion paths" above |
| **Right to restrict processing** | Deactivate the user (`isActive=false`); their data is retained but unreachable |
| **Right to data portability** | Same as right of access; output is JSON, machine-readable |
| **Right to object** | Email the support contact in [support.md](support.md) |
| **Right not to be subject to automated decisions** | Workflows that make a binding decision should include a `user-approval` node; this is your tooling, not ours, but Composer ships the primitive |

For CCPA: the same primitives map to "right to know," "right to delete," "right to opt out of sale" (we don't sell), and "right to non-discrimination" (Composer's plan tiers don't depend on the customer exercising privacy rights).

## Children

Composer is a B2B platform. We don't knowingly collect data from children under 16. If you discover an account owned by a minor, contact us and we'll deactivate.

## Changes to data handling

Material changes to what data we collect or where it goes (a new sub-processor, a new retention default, an opt-in default flipped to opt-out, etc.) are announced **30 days before they take effect** via:

- Update to this document with the change date in the heading
- A line in [`../../CHANGELOG.md`](../../CHANGELOG.md)
- An email to the primary contact on each managed customer's account

Customers can decline a change by leaving the platform within the notice window without penalty.

## Adjacent docs

- [security.md](security.md) — the controls that protect everything described here
- [compliance.md](compliance.md) — certification status + the audit trail of independent verification
- [legal/privacy-policy.md](legal/privacy-policy.md) — the customer-facing notice template
- [legal/subprocessors.md](legal/subprocessors.md) — the versioned list of third parties
- [legal/data-processing-addendum.md](legal/data-processing-addendum.md) — DPA template for customer agreements
