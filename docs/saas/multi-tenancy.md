# Multi-Tenancy & Isolation

> **Audience:** customer security teams, architects, anyone who needs to know "could another customer's workflow ever see our data?"
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Last reviewed:** 2026-07-21.

## TL;DR

Composer's deployment model today is **single-tenant per environment**. One Composer instance, one Postgres database, one Vercel/Fly host, one set of customer accounts — all belonging to a single customer organisation. There is no path by which Customer B's workflow can read Customer A's data because Customer A and Customer B never share a deployment.

A future **shared-tenant** offering — where multiple customer organisations live in one Composer instance — is on the roadmap. The codebase already has the primitives in place to support this: every `Workflow`, `WorkflowExecution`, `ApiKey`, `McpServer`, and `LlmApiKey` row is keyed by `userId`, and every read path enforces an authz check before returning data. Adding `organizationId` is a schema migration plus an authz-helper update; nothing about the architecture rules it out.

## Today's deployment topologies

### 1. Customer self-host (DIY)

You run Composer on your own infrastructure: your Postgres, your container host, your CDN, your secrets store. We provide the code, the runbooks, and operational consultation.

- **Tenancy boundary**: your VPC. Composer doesn't see anything outside what your workflows tell it to see.
- **What we can do**: nothing — we have no access. Bug fixes ship as new releases that you pull on your cadence.
- **What you control**: everything — including disaster recovery, monitoring, scaling, and access reviews. The runbooks under [`../operations/`](../operations/) are the recipes.

### 2. Composer-managed single-tenant

We provision a Composer environment dedicated to your organisation. Today this is the default for new deals.

- **Tenancy boundary**: a dedicated VPC + dedicated Postgres database + dedicated app host per customer. No shared compute, no shared storage.
- **Encryption keys** (`ENCRYPTION_KEY`, `JWT_SECRET`) are unique per deployment and never leave that deployment's secret store.
- **Access controls**: we don't read your data. Operational access for the operating team is logged separately and reviewed quarterly. Documented in the contract.
- **Customer signs**: ToS, DPA, optional BAA (if HIPAA is in flight — see [compliance.md](compliance.md)).

### 3. Embedded into a host platform

Composer slots into a larger internal platform as a module. Auth, tenancy, billing, and operational ownership roll up to the host platform's team.

- **Tenancy boundary**: IE's tenant model. Each IE customer maps to a Composer "deployment" boundary in the same way as a single-tenant managed deployment.
- **Auth**: NextAuth → IE-issued JWTs (Composer's "embedded" deployment mode). The `IEP_JWT_ISSUER` and `IEP_JWKS_URL` settings wire Composer to IE's trust store.
- **Customer signs**: the IE master agreement; Composer-specific addendum if needed.

## Why we're not multi-tenant yet

Three deliberate reasons:

1. **Blast radius minimisation.** Until we have SOC 2 Type II, formal data-loss-prevention tooling, and the audit-log primitives a shared-tenancy customer expects, a per-customer deployment is the honest answer. A bug or a misconfiguration in a single-tenant deployment hurts one customer; the same bug in a shared deployment could hurt all of them.
2. **Customer trust building.** Most early enterprise customers prefer the "your own database" answer. It's a stronger procurement story, and it lets us sleep better at night while we mature the platform.
3. **Operational simplicity.** Fewer moving parts in a single-tenant deployment means fewer dashboards, fewer paths to debug, faster onboarding.

The trade-off: a single-tenant deployment is more expensive per customer than shared, and less flexible for very small customers who'd benefit from a self-serve free tier. Shared tenancy will close those gaps when it ships.

## Path to shared tenancy

If a customer's procurement says "we're fine with shared," the path to delivering that without rewriting the codebase is well-defined. Below is the design we'd execute against.

### Schema additions

```sql
-- New table
CREATE TABLE organizations (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  slug        TEXT UNIQUE NOT NULL,
  plan        TEXT NOT NULL DEFAULT 'starter',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Augment existing tables
ALTER TABLE users               ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);
ALTER TABLE workflows           ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);
ALTER TABLE workflow_executions ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);
ALTER TABLE api_keys            ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);
ALTER TABLE mcp_servers         ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);
ALTER TABLE llm_api_keys        ADD COLUMN organization_id TEXT NOT NULL REFERENCES organizations(id);

-- Indexes (composite keys keep multi-tenant lookups efficient)
CREATE INDEX idx_workflows_org_user ON workflows (organization_id, user_id);
CREATE INDEX idx_executions_org_user ON workflow_executions (organization_id, user_id);
-- etc.
```

### Authz helper changes

Every existing helper (`Depends(get_current_user_id)`, `Depends(ensure_admin)`) gets an `organization_id` companion. The pattern is established already — `Depends(ensure_owner_or_admin(workflow_id))` — so the migration is mechanical.

### Postgres row-level security (defence in depth)

```sql
ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;

CREATE POLICY workflow_isolation ON workflows
  USING (organization_id = current_setting('app.current_org_id')::text);
```

The application sets `app.current_org_id` per-request. RLS catches the case where a code-level authz miss slips through; even if the application asks for the wrong org's data, Postgres refuses.

### Customer-facing changes

- **Sign-up flow** routes to an "organisation creation" step the first time, then "join an existing org" thereafter (invite-link).
- **Admin role** becomes scoped per-organisation. A "super-admin" role is reserved for the operating team (used only for incident response, fully logged).
- **Per-org settings**: plan tier, included quotas, billing contact, DPA signed-on date, data-residency region.
- **Per-org limits**: soft caps on workflows / executions / API keys, with overage handling per [pricing.md](pricing.md).

### What we'd test before shipping

A short list of acceptance tests we'd want green before turning on shared tenancy for any production customer:

1. Workflow read across organisations: A user in org A asking for a workflow in org B gets 404 (not 403).
2. External invoke across organisations: an `Authorization: Bearer ck_<orgB-key>` call to a workflow in org A 404s.
3. Admin override across organisations: an admin in org A cannot read or write any data in org B (only super-admin can, and only via a separately-logged path).
4. RLS pin: forge a request with `app.current_org_id` set to a different value than the JWT claims; Postgres refuses the read.
5. Search / list endpoints don't leak count or existence across orgs.
6. LangSmith trace tags include `organization_id` so we can filter in the trace UI.
7. Backups are restorable per-organisation (point-in-time restore + a per-org filter step in the restore script).

## Isolation primitives — what's already in the codebase

These hold today even though we don't ship shared tenancy:

| Primitive | Where | Used today | Multi-tenant ready |
|---|---|---|---|
| Per-row `userId` foreign key | All ownership tables | ✓ — used for owner-only delete and 404-on-cross-user | Yes — adds `organizationId` next to it |
| 404 on cross-tenant read | Every workflow + execution route | ✓ | Same routes; check switches from `userId` to `organizationId` |
| Bcrypt-hashed per-user API keys | `api_keys` | ✓ | Same |
| AES-256-GCM at rest for secrets | `llm_api_keys`, `mcp_oauth_tokens` | ✓ — single key per deployment | Per-org KEK is a near-term roadmap item; the encryption interface accommodates it |
| Authz dependency injection (`Depends`) | Every `src/api/*.py` route | ✓ | Same — we add an `Depends(get_current_org_id)` |
| 404 vs 403 policy | Every read | ✓ | Same |
| Owner-only delete | Workflows, MCP servers | ✓ | Same — admins still can't delete other users' data even within an org |
| Public workflow visibility | `workflows.is_public` | ✓ | Same — public would mean public *within the org*, not across orgs |
| Workflow assignment (many-to-many sharing) | `workflow_assignments` (`workflowId`, `userId`, `assignedById`) | ✓ — grants full read/write/run to a non-owner within the *same* deployment; owner-only fields (delete, reassign owner) are untouched by an assignment | Same — an assignment would still be scoped to users within one org; no cross-org grant path |

## Tenancy at the integrations layer

Even within a single-tenant deployment, customers care about how Composer's integrations isolate their data:

- **MCP OAuth tokens** are bound to the user who completed the OAuth flow. Even within one Composer deployment, User A's Highspot token isn't visible to User B unless the MCP server is explicitly marked `isShared`. The shared-token fallback ([decisions.md ADR-0008](../decisions.md)) is opt-in per-server, controlled by an admin.
- **LLM API keys** are deployment-scoped (one Anthropic key per deployment, not per user). Most customers want this — central billing, central rate limits, central audit. If a customer wants per-user keys instead, the `llm_api_keys` table accepts a `userId` column today and the dispatcher can fall back to it.
- **Vector DB connections** are workflow-scoped — the connection details live on the vector-DB node's config, encrypted in the workflow row. A workflow with a Pinecone connection only reads from the index that workflow specifies.
- **Document uploads** never leave the request — see [privacy.md](privacy.md).

## Cross-customer data leakage — the questions we get asked

| Question | Answer |
|---|---|
| Can another customer see our workflows? | No — they're on a different deployment. |
| Can another customer's prompt influence our LLM call? | No — your LLM keys are different keys; even if the LLM provider misroutes (which we have no evidence has ever happened), the API key authenticates the call. |
| If we share a vector DB region with another customer, can they read our embeddings? | No — vector DB scopes are per-index; you control the index name. You'd both have to use the same index name *and* the provider would have to misroute. |
| What about LangSmith projects? | Set `LANGCHAIN_PROJECT` to a unique value per deployment. Customers on the managed offering get this configured automatically. |
| Could an Anthropic / OpenAI training run see our prompts? | Per their commercial terms, no. We don't independently verify; check each provider's data-policy page for the contractual answer. |

## When to push back on a customer asking for shared tenancy

Sometimes a customer asks for "shared tenancy because it's cheaper" without realising the trade-off. Three things to surface in the conversation:

1. **Today, single-tenant managed has a higher per-deployment cost but lower per-environment risk.** If the customer's workload doesn't need free-tier economics, single-tenant is the more conservative choice.
2. **Shared tenancy will not be cheaper than self-hosting** for customers with their own platform team. Self-host is the cost-floor option.
3. **Mid-size and enterprise customers' own legal teams almost always prefer single-tenant** for the procurement story. Even if engineering is comfortable, the contract language is harder.

If after that conversation the customer still wants shared, that's signal that shared tenancy is genuinely valuable to them — and we should weight it on the roadmap accordingly.

## Adjacent docs

- [security.md](security.md) — the controls each tenancy model relies on
- [compliance.md](compliance.md) — what changes in audit posture as tenancy models shift
- [`../decisions.md`](../decisions.md) — ADR-0014 (deployment-mode toggle), ADR-0015 (dev-mode auth), ADR-0023 (LLM key catalog)
- [pricing.md](pricing.md) — how plans relate to tenancy choice
- [roadmap.md](roadmap.md) — when shared tenancy ships
