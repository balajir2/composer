# Security

> **Audience:** prospective customer security teams, internal SREs, vulnerability researchers.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Last reviewed:** 2026-05-04. Engineering source of truth: [`../decisions.md`](../decisions.md).

This is what we promise about Composer's security, what we've built to deliver on it, and how to report a problem if you find one.

## Security posture in one paragraph

Composer includes meaningful security foundations: encrypted LLM keys and OAuth tokens, bcrypt-hashed passwords and API keys, role-based authorization, size limits, sandboxed expressions, isolated code execution, and MCP response sanitization. Production security still depends on deployment configuration and network controls. Known hardening work—including centralized credentials, HTTP-node SSRF defenses, scanner-safe approval confirmation, and distributed rate limiting—is tracked in the [Improvement Backlog](../claude-improvement-backlog.md). Composer is not currently SOC 2 certified; see [compliance.md](compliance.md).

## Threat model

### Assets we protect

| Asset | Sensitivity | Where it lives |
|---|---|---|
| Customer LLM API keys | High — direct billing impact if leaked | Postgres `llm_api_keys` (AES-256-GCM); optionally synced to runtime env vars |
| MCP OAuth tokens (per-user, per-server) | High — give a workflow access to the user's external accounts | Postgres `mcp_oauth_tokens` (AES-256-GCM) |
| Per-user API keys (`ck_...`) | High — authorise external invocations of published workflows | Postgres `api_keys` (bcrypt-hashed; plaintext shown once at creation) |
| User passwords (standalone mode only) | High | Postgres `users.password_hash` (bcrypt) |
| Workflow definitions | Medium — may encode business logic | Postgres `workflows` (plaintext; not customer secrets) |
| Execution inputs / outputs | Variable — depends on what the workflow processes | Postgres `workflow_executions`, `langgraph_checkpoints`; LangSmith if tracing enabled |
| Audit log of executions, approvals, key usage | Medium — disclosure shows operational pattern | Postgres |
| Customer documents uploaded for extraction | Low (transient) | **Never persisted** — read in memory, text extracted, bytes released |
| Source code of Composer | Public — repo `balajir2/composer` | n/a |

### Adversaries we plan against

1. **External attacker without credentials.** Cannot reach private workflows or executions; cannot enumerate users; cannot brute-force passwords (bcrypt cost factor + rate limits). Public workflows are world-readable by design.
2. **External attacker with stolen credentials (one user).** Can do what that user can do — read/write their own workflows, run published workflows their key authorises. Cannot escalate to admin without a second credential set. The blast radius is one user's data; admin operations remain protected.
3. **Authenticated member targeting another member's data.** Returns **404, not 403**, on any cross-user read so existence isn't leaked. Admin overrides exist for incident response (see [admin-guide.md](../admin-guide.md)) but are owner-only on **delete** specifically — even an admin can't accidentally erase another user's workflow.
4. **Malicious workflow author.** `simpleeval` rejects dunders, imports, and direct `eval`/`exec`; code execution uses the external E2B sandbox. The HTTP node is intentionally powerful and currently requires deployment-level egress controls to prevent access to internal or metadata endpoints until application-level SSRF defenses land.
5. **Malicious MCP server.** MCP output is treated as untrusted model input. The base64 blob sanitiser ([src/mcp/sanitize.py](../../src/mcp/sanitize.py)) removes a known class of oversized embedded binary payload; broader response-size and tool-result policies remain hardening work.
6. **Compromised LLM provider returning malicious tool calls.** Tool calls are validated against the registered tool's JSON schema before execution; the executor only invokes tools the workflow explicitly enables; HTTP nodes use the customer's tools, not the model's free will.
7. **Operator with database access.** Sees encrypted secrets, plaintext workflow definitions, plaintext execution logs (LangSmith too). For deployments where this is unacceptable, see "Bring your own encryption key" below.

### Out of scope (we deliberately don't defend)

- **Side-channel attacks against the LLM provider.** What the model writes into its tool calls is the model's choice. Customers concerned about prompt injection should use the `guardrails` node (PII / moderation / jailbreak / hallucination classifiers) on inputs **and** outputs. See [designer-guide.md](../designer-guide.md) for the patterns.
- **Compromised customer browser.** A user with malware on their machine can do anything that user can do; we don't try to defeat client-side compromise.
- **Insider threat at Bounteous (managed deployments).** Documented separately in the access-control runbook; relevant to managed customers, not to the codebase.

## What we encrypt and how

### Secrets at rest

| Field | Algorithm | Key source | Rotation |
|---|---|---|---|
| LLM API keys (`llm_api_keys.encrypted_key`) | AES-256-GCM | `ENCRYPTION_KEY` env var (32-byte, hex-encoded) | Manual via admin UI; the key never appears in plaintext after first save |
| MCP OAuth tokens (`mcp_oauth_tokens.encrypted_*_token`) | AES-256-GCM | Same `ENCRYPTION_KEY` | Refresh tokens automatically rotate per OAuth provider's policy |
| User passwords | bcrypt (cost 12) | n/a (one-way) | Customer-driven (password reset flow) |
| Per-user API keys | bcrypt (cost 12) | n/a (one-way) | Manual via runs page; old keys can be revoked at any time |
| Composer JWTs | HS256 signed with `JWT_SECRET` | Single secret per deployment | Manual (rotating invalidates active sessions) |
| Frontend session tokens | NextAuth's encrypted JWT | NextAuth's `AUTH_SECRET` | Manual; rotating logs everyone out |

The `ENCRYPTION_KEY` and `JWT_SECRET` are **per-deployment**, not shared across customers. For managed-single-tenant customers, those keys are generated per customer at provisioning time and never leave the deployment's secret store.

**Bring-your-own-encryption-key** is on the [roadmap](roadmap.md). The current design accommodates a future KMS-backed `Encryptor` interface; the call sites in `src/security/encryption.py` are already abstracted.

### Secrets in transit

- **TLS 1.2+** required for all API + WebSocket traffic. Backend serves HTTP-only when bound to `localhost` for local dev; production deployments terminate TLS at the load balancer or CDN (Vercel / Cloudflare / Fly proxy).
- **MCP and HTTP egress** should use HTTPS in production. Operators must enforce network egress and destination policy at the deployment layer; application-level destination controls are incomplete.
- **LLM provider calls** use the providers' official HTTPS endpoints.

### Secrets in memory

Encrypted secrets are decrypted when needed by an integration. Application code should avoid logging
decrypted values, but Python does not provide a guaranteed memory wipe. Operators should treat
process memory and diagnostic dumps as sensitive. Additional argument and credential redaction is
tracked in the improvement backlog.

## Authentication

Three layers stack:

1. **NextAuth v5 (frontend session).** Azure AD via `AzureAD` provider, or Credentials provider for username/password. NextAuth signs an encrypted JWT for the browser session.
2. **Composer JWT (backend session).** HS256, mints from NextAuth's claims. 8-hour access token; 30-day refresh token, rotates on every `/auth/refresh` call so active users never see a re-auth prompt. Idle users re-auth after 30 days.
3. **Per-user API keys** (`ck_<bcrypt-hashed>`). Created via the runs page, shown plaintext once, used in `Authorization: Bearer ck_...` headers for `POST /api/run/{slug}`. Owners can revoke at any time; revoked keys 401 immediately.

Standalone deployments accept username/password registration; SSO-enabled deployments can require Azure AD with no fallback.

## Authorisation

Role is one bit on `users.role` — `admin` or `member`. The default for new users is `member`. Admins are promoted via SQL ([operations/admin-operations.md](../operations/admin-operations.md)) — there's no UI to self-promote.

| Resource | Member access | Admin access |
|---|---|---|
| Own workflows | Read / write / publish / delete | Same |
| Other user's *public* workflow | Read; clone via "Use as template" | Read |
| Other user's *private* workflow | **404** (existence hidden) | Read / update / publish |
| Delete *any* workflow | Owner only | **Owner only** — admins cannot delete other users' workflows by design (audit trail consideration) |
| Reassign workflow owner | n/a | `PATCH /workflows/{id}/owner` |
| Run a public workflow | Anyone with a valid API key | Same |
| Run a private workflow | Owner key only | Owner or admin key |
| Approval queue | Own approvals | All approvals (admin override for stuck queues) |
| User management | n/a | `/admin/users` |
| LLM key catalog | Read enabled models | Full CRUD + Verify |
| MCP server `isShared` toggle | n/a | Per-server flag |
| Deployment settings | n/a | `/admin/deployment-settings` |

The 404-vs-403 choice ([decisions.md ADR-0025](../decisions.md)) is intentional: returning 403 leaks the existence of an object an attacker shouldn't know exists. 404 means "I have nothing to show you," indistinguishable from "this id doesn't exist."

## Input validation

| Surface | Cap | Source |
|---|---|---|
| Workflow nodes per workflow | 100 | `Settings.max_workflow_nodes` |
| Workflow edges per workflow | 200 | `Settings.max_workflow_edges` |
| Execution input bytes (`POST /executions`, `POST /api/run/{slug}`) | 1,000,000 | `Settings.max_execution_input_bytes` |
| Document upload bytes | 10,485,760 (10 MB) | `MAX_UPLOAD_BYTES` in `src/api/uploads.py` |
| `while` loop iterations | 100 | Hard-coded engine cap |
| Agent tool-call iterations | 10 | Per-execution cap; failure raises explicit error |

Beyond size caps, every Pydantic model on the API surface is `extra="forbid"` — unknown fields reject the request. Workflow shape is validated by a discriminated union over node `type`; an unknown type is a 422.

## Rate limits

In-memory token-bucket per route:

| Route | Default | Setting |
|---|---|---|
| `POST /executions` | 30 / min | `rate_limit_executions_per_minute` |
| `POST /executions/{id}/resume` | 60 / min | `rate_limit_resume_per_minute` |
| `POST /api/run/{slug}` | 60 / min | `rate_limit_api_run_per_minute` |
| `POST /auth/login` | 10 / min | `rate_limit_login_per_minute` |
| `POST /auth/register` | 5 / min | `rate_limit_register_per_minute` |
| `POST /auth/refresh` | 30 / min | `rate_limit_refresh_per_minute` |
| `POST /mcp-servers/{id}/test` | 10 / min | `rate_limit_mcp_test_per_minute` |
| `POST /uploads/extract-text` | 20 / min | hard-coded |

The bucket is keyed per actor (user id, IP address for unauthenticated routes, API key id for external invokes). For deployments behind multiple replicas a Redis-backed limiter is on the [roadmap](roadmap.md); the in-memory limiter is correct per-process and conservative enough that misuse can't sustain a real DoS at any plausible replica count.

## Network egress

Composer's HTTP node will call any URL the workflow author specifies. **You should restrict outbound traffic at the network layer** if your threat model requires it:

- Block egress to RFC 1918 / link-local / metadata-service addresses (`169.254.169.254`, `127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`).
- For Vercel-hosted backends, this is automatic for the metadata-service ranges. For Fly / containers in your VPC, configure your egress proxy or security group accordingly.
- The `tools.<name>.enabled` deployment setting can block specific built-in tool providers (e.g. disable Browserless if your security team won't approve headless Chrome).

The application enforces no allowlist by default — that's a network-layer concern. We can ship one if your contract requires it.

## Logging & audit trail

Every meaningful action is logged with the calling user id, request ip, timestamp, and outcome. Locations:

- **Backend logs**: structured JSON via Python's `logging` module → your container host (Fly logs, CloudWatch, etc.). Set `log_level=INFO` for production; `DEBUG` produces request-body logs that may include execution input.
- **Postgres tables** that double as audit trails:
  - `workflow_executions` — every run, with status / inputs / outputs / errors / timestamps / actor
  - `approvals` — every human-approval decision (approved/rejected/pending), with the deciding user id and rationale
  - `api_keys.last_used_at` — touched on every successful external invoke; lets you spot stale or compromised keys
- **LangSmith** (optional) — full execution trace, prompt-by-prompt, tool-by-tool. Useful for security review and incident analysis.

Log retention is whatever your container host + Postgres backups dictate. We don't enforce retention from the application; configure it operationally based on your compliance posture.

## Vulnerability reporting

If you've found a security issue in Composer, please report it directly rather than filing a public GitHub issue.

**Email**: `balajirajan@gmail.com` (Composer is authored and operated by Balaji Rajan; for self-hosted deployments, replace this with your own security contact)

**What to include:**
- A clear description of the issue
- Steps to reproduce, ideally with a minimal proof of concept
- The Composer commit / version you tested against
- The impact you observed (data exfiltration, privilege escalation, RCE, DoS, etc.)
- Whether you'd like credit in the postmortem

**What to expect:**
- Acknowledgement within 2 business days
- Triage decision (severity + initial action) within 5 business days
- Coordinated disclosure: we will not publicly discuss the vulnerability before a fix is available, and we ask the same of reporters
- For critical issues, an emergency patch path that bypasses the normal release cadence

We do not currently run a paid bug bounty. Researchers who provide constructive reports are credited in [`../../CHANGELOG.md`](../../CHANGELOG.md) (with permission) and in postmortems under [`../archive/incident-history/`](../archive/incident-history/).

## Security controls by category — quick reference

| Control | Status | Where it lives |
|---|---|---|
| TLS 1.2+ everywhere | ✓ | Edge / load balancer |
| AES-256-GCM at rest for secrets | ✓ | `src/security/encryption.py` |
| bcrypt for passwords + API keys | ✓ | `src/security/passwords.py`, `src/security/api_keys.py` |
| RBAC | ✓ | `Depends(ensure_admin)` in admin routes |
| Input size caps | ✓ | `src/config.py` |
| Rate limiting (per-actor) | ✓ | `src/security/rate_limit.py` |
| Sandboxed expression eval | ✓ | `src/executors/_eval.py` (simpleeval) |
| Sandboxed code execution | ✓ | `e2b_code_interpreter` |
| MCP OAuth (RFC 8707 resource binding) | ✓ | `src/mcp/oauth.py` |
| MCP base64 blob sanitisation | ✓ | `src/mcp/sanitize.py` |
| 404-on-cross-tenant-read | ✓ | Every workflow + execution route |
| Stuck-execution sweeper | ✓ | `src/maintenance/execution_sweeper.py` |
| Comprehensive audit log | Partial — DB tables yes, separate audit-log table on roadmap | `workflow_executions`, `approvals`, `api_keys` |
| Bring-your-own KMS / KEK | Roadmap | `src/security/encryption.py` is abstracted to support this |
| WAF / DDoS protection | Customer's CDN / load balancer | n/a (no application-layer WAF) |
| SOC 2 Type II | In progress | See [compliance.md](compliance.md) |

## Adjacent docs

- [compliance.md](compliance.md) — formal certification status + customer commitments
- [privacy.md](privacy.md) — what data we hold, retention, sub-processors, deletion paths
- [`../decisions.md`](../decisions.md) — engineering ADRs that explain why each control is built the way it is
- [`../admin-guide.md`](../admin-guide.md) — the admin operations that depend on these controls (key rotation, user management)
- [`../archive/incident-history/`](../archive/incident-history/) — past defect-class write-ups + postmortems
