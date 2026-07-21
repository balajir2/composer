# Composer Improvement Backlog for Claude Code

## Purpose

This document is a review and implementation backlog for improving Composer's reliability,
security, functionality, technical architecture, and usability. Claude Code should read the
repository before accepting the priorities below, verify each finding against the current code,
and then propose an execution order based on impact, dependencies, risk, and effort.

Do not treat this document as permission to implement every item in one change. Work on one
approved backlog item at a time and keep changes small, testable, and reversible.

## Instructions for Claude Code

Before prioritizing or implementing anything:

1. Read `CLAUDE.md`, `README.md`, `docs/architecture.md`, and relevant ADRs in
   `docs/decisions.md`.
2. Run `git status --short` and preserve all existing user changes.
3. Inspect the current implementation and tests for every item being evaluated.
4. Call out findings that are already fixed, partly implemented, or contradicted by the code.
5. Rank remaining work using:
   - User and business impact.
   - Security and data-loss risk.
   - Production reliability.
   - Dependency on other backlog items.
   - Implementation and migration risk.
   - Testability and rollback complexity.
6. Present the proposed priority order before editing code.
7. Implement only the item explicitly selected by the user.
8. Add or update tests for every behavior change.
9. Run targeted verification first, then the appropriate broader checks.
10. Update documentation and `CHANGELOG.md` for material behavior or configuration changes.

## Initial recommendation

The initial proposed order is:

1. Fix frontend/backend node field-contract bugs and add contract tests.
2. Workflow variable-reference validation.
3. Structured action outcomes and semantic success policies.
4. Outcome-aware downstream notifications.
5. Production configuration validation.
6. Centralized credential management.
7. Outbound HTTP request security controls.
8. Protect workflow state and execution-input boundaries.
9. Scanner-safe and race-safe approval confirmation.
10. Durable execution workers and idempotency.
11. Shared events and distributed rate limiting.
12. Execution trace and dry-run usability.
13. Dependency and persistence-stack modernization.

Claude Code should validate this order rather than accepting it blindly.

---

## Audit status (2026-07-11)

> **2026-07-21 update:** most of the "Confirmed runtime and security defects" below were fixed in
> the 2026-07-12 Codex-audit remediation pass (P0-1, P0-2, P0-6, P0-7, P1-1, P1-6) or the 2026-07-13
> credential-disclosure patch (P0-5, partial). The "Confirmed node-contract defects" list (P0-0) and
> the Start/End terminal-path-validation portion of P0-8 remain open. See each numbered section
> below for its individual resolved-status note and commit reference.

This backlog was expanded after a systematic static audit of:

- All currently registered frontend node property panels.
- Backend Pydantic node aliases and executor field reads.
- Graph validation and compilation.
- Execution creation, completion, sync invocation, approval, resume, and deletion paths.
- Jira, MCP, Arcade, HTTP, email, vector DB, and LLM tool-call behavior.
- Workflow, execution, MCP, OAuth, WebSocket, secret-storage, and deployment boundaries.

Verification performed during the audit:

```text
364 targeted backend tests passed
frontend TypeScript type-check passed
```

The passing suite does not disprove the findings below. Most findings identify missing contract,
race, security-boundary, and adversarial tests that the existing suite does not exercise.

### Confirmed node-contract defects

- Arcade, HTTP, User Approval, Extract, and Join Chunks panels use fields that do not match their
  backend schemas.
- Data Transform cannot configure fields required by its executor and describes the wrong
  expression language.
- End exposes `outputRenderHint`, but `EndNodeData` and `EndExecutor` do not define or consume it.

### Confirmed runtime and security defects

- Unresolved variables can reach actions as literal placeholders.
- Action nodes can complete without performing their intended action.
- Multiple mutating tool calls may execute concurrently without dependency ordering.
- External input can inject undeclared/protected-looking state and override Jira connection data.
- Falsey outputs can be discarded by boolean fallback logic.
- In-app approval resume is not an atomic single-winner transition.
- HTTP requests lack an SSRF policy and bounded response handling.
- MCP OAuth client secrets are stored plaintext inside `oauthConfig` JSON.
- MCP custom headers are returned through read APIs and may contain secrets.
- Public/shared workflow configuration can expose non-Jira inline credentials because redaction is
  Jira-specific.
- Graph reachability and compiled reachability disagree around visual-only nodes.
- Start/End edge direction and executable paths to an End node are insufficiently validated.
- Sync external invocation can report a paused approval run as `running` after timeout.
- Execution input limits count characters rather than UTF-8 bytes.

---

## P0-0: Fix frontend/backend node field contracts

> **Status (2026-07-21): not started.** Not part of the 2026-07-12 Codex-audit remediation pass (see
> CHANGELOG.md's "Fixed — Codex audit remediation" entry, which covers P0-1 through P1-6 but not
> P0-0) and not in `docs/deferred-backlog.md`. Still open.

### Problem

Several property panels write field names that do not match the backend workflow models. The
property panel forwards node-data patches directly, so there is no mapping layer that converts the
frontend names into backend aliases. Because workflow node data permits extra fields, unexpected
values can survive as extras while the real configuration fields keep empty defaults. This can
make a node appear configured in the designer while it fails, uses a fallback, or runs with missing
configuration on the backend.

The current mismatches are:

| Node | Frontend currently writes | Backend expects |
|---|---|---|
| Arcade | `toolName` | `arcadeTool` |
| Arcade | `args` as textarea text | `arcadeInput` as an object/dictionary |
| Arcade | No user-ID control | `arcadeUserId` |
| HTTP | `method` | `httpMethod` |
| HTTP | `url` | `httpUrl` |
| HTTP | `headers` as textarea text | `httpHeaders` as an object/dictionary |
| HTTP | `body` as textarea text | `httpBody` as parsed JSON, text, list, or object |
| HTTP | No response-path control | `responsePath` |
| User Approval | `message` | `approvalMessage` |
| Extract | `inputVariable` | `input` |
| Extract | `schema` as textarea text | `jsonSchema` as an object/dictionary |
| Extract | No model control | `model` (optional) |
| Join Chunks | `inputVariable` | `joinChunksVariable` |
| Join Chunks | `separator` | `joinChunksSeparator` |
| Join Chunks | `prefix` | `joinChunksPrefix` |
| Join Chunks | `suffix` | `joinChunksSuffix` |
| Join Chunks | No metadata control | `joinChunksIncludeMetadata` |

The Data Transform panel uses the correct `expression` field, but it exposes only that field. Its
executor also requires `collection` and supports `operation`, `itemVar`, and `initial`. A node built
through the current UI therefore retains an empty collection expression and fails at runtime.

The End panel writes `outputRenderHint`, but `EndNodeData` has no such modeled field and
`EndExecutor` does not consume it. Decide whether this is intended frontend-only presentation
metadata; if so, model and consume it explicitly. Otherwise remove the no-op control.

### Evidence to verify

- `frontend/components/composer/canvas/node-panels/arcade.tsx`
- `frontend/components/composer/canvas/node-panels/http.tsx`
- `frontend/components/composer/canvas/node-panels/user-approval.tsx`
- `frontend/components/composer/canvas/node-panels/extract.tsx`
- `frontend/components/composer/canvas/node-panels/join-chunks.tsx`
- `frontend/components/composer/canvas/node-panels/data-transform.tsx`
- `frontend/components/composer/canvas/property-panel.tsx`
- `src/engine/workflow.py` (`ArcadeNodeData` and `HttpNodeData`)
- `src/executors/arcade.py`
- `src/executors/http.py`

Claude Code must re-check these files before changing anything in case another worktree change has
already corrected part of the contract.

### Objective

Make data saved by every affected property panel exactly match the backend workflow schema and add
systematic tests that prevent future frontend/backend contract drift.

### Requirements

#### Arcade panel

- Read and write `arcadeTool`, not `toolName`.
- Read and write `arcadeInput`, not `args`.
- Parse the Arcade arguments textarea as JSON and store an object rather than the raw textarea
  string.
- Add an `arcadeUserId` field with clear guidance that it identifies the end user whose OAuth
  connection Arcade should use.
- Support variable substitution in string values inside `arcadeInput` and in `arcadeUserId` if the
  backend contract allows it. If `arcadeUserId` currently lacks substitution, either implement and
  test it or clearly document that it is literal; do not imply unsupported behavior in the UI.
- Show inline JSON validation errors and do not silently discard invalid JSON.
- Explain in the panel that Composer may pause the execution until the user completes OAuth.
- Explain that the backend requires `ARCADE_API_KEY`.
- Fix the executor/documentation mismatch for `arcadeUserId`: the designer guide recommends a
  variable expression, but the executor currently reads the value literally without substitution.
- Do not default production workflows to the shared identity `workflow-builder`. Require or derive
  a stable per-user identity so different users cannot accidentally share one Arcade authorization
  namespace.

#### HTTP panel

- Read and write `httpMethod`, `httpUrl`, `httpHeaders`, and `httpBody`.
- Add a `responsePath` field for selecting a nested value from a JSON response.
- Parse header JSON into `Record<string, string>` and validate that every value is a string.
- Parse valid JSON bodies into objects/lists/scalars as supported by the backend.
- Preserve a deliberately entered raw-text body instead of forcing all bodies to JSON.
- Show inline validation errors for malformed header JSON and malformed bodies when JSON mode is
  selected.
- Make the body mode explicit (`JSON` or `Raw text`) if that produces a clearer and safer contract.
- Retain variable placeholders during parsing and saving.

#### User Approval panel

- Read and write `approvalMessage`, not `message`.
- Migrate legacy `message` only when `approvalMessage` is absent.
- Verify the exact configured message appears in the interrupt payload and approval email.
- Keep `approverEmail` and `approverCc` unchanged because they already match the backend aliases.

#### Extract panel

- Read and write `input`, not `inputVariable`.
- Read and write `jsonSchema`, not `schema`.
- Parse schema text into an object and show inline validation errors.
- Preserve the intended fallback to `{{lastOutput}}` only when the input is genuinely empty.
- Consider exposing the optional model selector using the same live model catalogue as other LLM
  nodes; if deferred, document the backend default clearly.
- Confirm whether `extractConfig` and `extractTool` are still active contracts. Remove dead fields
  in a separately approved compatibility change or expose them if they are required.

#### Join Chunks panel

- Read and write `joinChunksVariable`, `joinChunksSeparator`, `joinChunksPrefix`, and
  `joinChunksSuffix`.
- Add a `joinChunksIncludeMetadata` control.
- Make it clear that the input is a state variable name containing a list, not a mustache template.
- Migrate the legacy short field names without overwriting canonical values.

#### Data Transform panel

- Expose `operation` with map/filter/reduce choices.
- Expose the required `collection` expression.
- Expose `expression` and `itemVar` with operation-specific guidance.
- Expose `initial` when reduce is selected, with a typed JSON/raw-value input strategy.
- Replace the current inaccurate help text describing the field as JSONPath/Handlebars; the
  executor evaluates simpleeval expressions.
- Add designer-side required-field validation matching executor requirements.

#### End panel

- Define the intended contract for `outputRenderHint`.
- If it controls presentation, add it to the backend schema/API and ensure execution-result views
  use it consistently.
- If it has no supported behavior, remove the field from the panel rather than saving a silent
  no-op option.

#### Compatibility and migration

- Detect legacy node data using the mismatched short names documented above.
- Either migrate legacy fields when loading/saving or provide a one-time backend migration. Choose
  one documented strategy and test it.
- Do not overwrite correctly configured canonical fields with empty legacy values.
- Remove legacy keys after a successful canonical migration so the saved workflow has one source
  of truth.
- Confirm that API serialization uses the backend aliases expected by the frontend.

#### Contract protection

- Add frontend tests that edit every field and assert the exact node-data patch produced.
- Add a round-trip test: frontend-shaped workflow JSON -> backend Pydantic validation -> serialized
  workflow response -> frontend panel values.
- Add backend model tests proving canonical fields populate the executor-facing attributes.
- Consider generating or importing panel field types from the OpenAPI client instead of relying on
  untyped `Record<string, unknown>` keys.
- Reassess `BaseNodeData(extra="allow")`. It is the reason misspelled mature-node fields can pass
  silently. Prefer `extra="forbid"` for fully implemented node schemas, or isolate legacy/forward-
  compatibility data behind an explicit extension field rather than accepting arbitrary keys.
- Audit every remaining node panel against its Pydantic aliases and executor reads. Produce a
  checked mapping table in the implementation notes. Report additional mismatches before silently
  expanding the implementation scope.

### Acceptance criteria

- An Arcade node configured in the UI persists `arcadeTool`, `arcadeInput`, and `arcadeUserId` and
  reaches `ArcadeExecutor` with the same values.
- Arcade arguments are stored as an object and invalid JSON produces a visible UI error.
- An HTTP node configured in the UI persists `httpMethod`, `httpUrl`, `httpHeaders`, `httpBody`, and
  `responsePath` and reaches `HttpExecutor` with the same values.
- HTTP headers are stored as a string-valued object, not raw textarea text.
- HTTP raw-text and JSON request bodies both survive save/load and execute using the intended
  request encoding.
- A User Approval message configured in the UI is the message shown to reviewers and emailed.
- Extract input and schema survive save/load and reach `ExtractExecutor` in canonical form.
- Join Chunks configuration survives save/load and reaches `JoinChunksExecutor` in canonical form.
- A Data Transform node can configure and successfully run map, filter, and reduce from the UI.
- Existing legacy Arcade and HTTP nodes are migrated or remain usable through an explicitly tested
  compatibility path.
- Frontend type-check, relevant component tests, backend workflow-model tests, and executor tests
  pass.
- Documentation explains the conceptual distinction: Arcade is a managed OAuth tool gateway;
  HTTP is a direct request where the workflow owns URL, headers, authentication, and body.

### Suggested verification

Run the repository's existing equivalents of:

```text
pytest tests/unit/engine/test_workflow_models.py
pytest tests/unit/executors/test_arcade.py tests/unit/executors/test_http_executor.py
frontend type-check
frontend component tests for ArcadePanel and HttpPanel
frontend production build
```

Use the actual test filenames and package-manager commands discovered in the repository. Do not
create parallel duplicate test files when an appropriate existing test module already exists.

### Scope guard

This task fixes the Arcade/HTTP configuration contract and its usability. It does not include the
larger credential-store redesign, durable workers, or general action-outcome architecture. Record
new discoveries as follow-up backlog items unless they are necessary to make this contract work.

---

## P0-1: Validate workflow variable references

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `f723efb`) as part of the Codex-audit
> remediation pass — see CHANGELOG.md.

### Problem

Unresolved `{{variable}}` placeholders remain literal at runtime. A workflow can therefore run
successfully while passing an unresolved placeholder to an LLM or external action. The observed
example defined a Start input named `MB` while the Jira node referenced `{{jira_project_key}}`.

### Objective

Prevent invalid variable references from reaching execution or production publishing.

### Requirements

- Extract references from every node field that supports variable substitution.
- Build the available-variable catalogue from Start inputs, upstream named node outputs,
  structured-output schemas, Set State nodes, and documented built-in variables.
- Detect unknown variables, downstream references, duplicate aliases, invalid paths, and empty
  references.
- Return structured errors containing node ID, field path, placeholder, and explanation.
- Validate before draft execution and publishing.
- Show errors in the designer and identify the affected node/property.
- Preserve the substitution engine's compatibility behavior unless a contract change is approved.
- Standardize recursive substitution for structured values. Arcade input and deterministic MCP
  arguments currently substitute only top-level string values, while other nodes use recursive
  substitution. Nested dictionaries and lists should behave consistently and have tests.

### Acceptance criteria

- The `MB` versus `jira_project_key` mismatch is caught before execution.
- Valid upstream references continue to work.
- Invalid workflows cannot be published.
- Draft runs return actionable validation errors.
- Backend and frontend tests cover valid, missing, downstream, and ambiguous references.

### Likely areas

- `src/variable_substitution.py`
- `src/engine/workflow.py`
- `src/engine/graph_builder.py`
- `src/api/workflows.py`
- `src/api/executions.py`
- Designer variable picker, canvas, and property-panel components

---

## P0-2: Add structured action outcomes and semantic success policies

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `8ccf91b`) as part of the Codex-audit
> remediation pass — `ToolCallRecord` + `action_policy` (`require_tool_call` /
> `require_successful_tool_call`), applied to Jira first. See CHANGELOG.md.

### Problem

Agentic action nodes can finish with explanatory text without performing the requested action.
Tool exceptions may also be returned to the model as observations, after which the node can still
be marked completed.

### Objective

Distinguish technical completion from successful business action.

### Requirements

- Record every tool call with sanitized arguments, status, structured result, external resource
  identifiers, and safe error details.
- Introduce action policies such as:
  - `best_effort`
  - `require_tool_call`
  - `require_successful_tool_call`
- Support `minimumSuccessfulCalls` for workflows expecting multiple actions.
- Return structured outputs including summary, tool-call count, success count, failure count,
  warnings, and created/updated resource identifiers.
- Fail the node when its configured success policy is not satisfied.
- Apply the pattern first to Jira, then generalize it for MCP, agent tools, email, HTTP mutations,
  and Arcade.
- Do not automatically execute multiple state-changing tool calls concurrently. The Agent, MCP,
  and Jira loops currently use `asyncio.gather` for all calls in one model turn. Add an explicit
  execution policy or classify read-only versus mutating tools; preserve order for dependent or
  mutating calls and allow parallelism only where independence is known.
- Sanitize tool arguments before logging. Jira and MCP currently log full argument dictionaries,
  which can contain customer content, PII, document text, comments, or tool-specific secrets.

### Acceptance criteria

- A clarification response is not mistaken for successful issue creation.
- A failed Jira API call cannot silently satisfy `require_successful_tool_call`.
- Read-only/search workflows can use `best_effort`.
- Jira issue keys appear as structured execution results.
- Tests cover no-call, failure, partial success, and full success.

### Likely areas

- `src/executors/jira.py`
- `src/tools/providers/jira.py`
- `src/executors/mcp.py`
- `src/executors/agent.py`
- `src/engine/workflow.py`
- Jira property panel and executor tests

---

## P0-3: Make downstream notifications outcome-aware

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 as part of the Codex-audit remediation pass —
> the live "BRD to Jira Tickets + Notification" production workflow now checks P0-2's structured
> outcomes before sending its success notification. See CHANGELOG.md.

### Problem

An email can claim that tickets were created even when the upstream Jira node performed no
successful create operation.

### Objective

Ensure downstream branches and messages reflect verified action outcomes.

### Requirements

- Expose structured Jira outcomes to downstream nodes.
- Route successful, partial, and failed outcomes separately.
- Update the BRD-to-Jira workflow/template so a zero-success outcome cannot reach an email titled
  "New Jira tickets created."
- Include actual issue keys and titles in successful notifications.
- Include safe, actionable diagnostics in failure notifications.
- Never expose credentials or raw authorization data.

### Acceptance criteria

- Zero created tickets cannot produce a success notification.
- Partial success is visibly different from complete success.
- Successful notifications list verified Jira keys.
- Template tests cover success, partial success, and failure.

---

## P0-4: Harden production configuration and deployments

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `311828f`) — `validate_production_config()`
> (`src/config_validation.py`) runs at FastAPI startup and refuses to boot with an unsafe production
> configuration. See CHANGELOG.md.

### Problem

The backend can use development defaults in production. This previously caused approval links to
redirect to `http://localhost:3000` because the deployment updated frontend origins but did not
pass `COMPOSER_FRONTEND_URL` to the backend service.

### Objective

Fail fast when production configuration contains unsafe or development-only values.

### Requirements

- Pass `COMPOSER_FRONTEND_URL` to the backend in the GCP deployment workflow.
- Reject localhost frontend/backend/callback URLs in production.
- Validate required JWT, encryption, database, and public URL configuration without logging
  secret values.
- Add safe environment diagnostics for administrators.
- Add post-deployment smoke tests for approval and password-reset redirects.

### Acceptance criteria

- Production startup fails clearly when a public URL points to localhost.
- Approval redirects use the configured production frontend.
- Later deployments preserve the correct setting.
- Development defaults continue working locally.

### Likely areas

- `src/config.py`
- `.github/workflows/deploy-gcp.yml`
- `src/api/approval_email.py`
- Authentication and password-reset link generation
- Production deployment documentation

---

## P0-5: Centralize and protect workflow credentials

> **Status (2026-07-21): partially resolved.** The live plaintext-disclosure gap (vector-DB `apiKey`/
> `embeddingApiKey`, HTTP node headers, MCP server headers, `oauthConfig.clientSecret`) was patched
> 2026-07-13 via generic `encrypt_marked`/`decrypt_marked` + header-redaction primitives in
> `src/security/encryption.py` (ADR-0032). The first-class `Credential`/`Connection` model this
> section also asks for is still an open design decision — see `docs/deferred-backlog.md` §P0-5.

### Problem

Jira tokens receive special encryption/redaction treatment, but other secret-bearing fields such
as vector database keys, embedding keys, and HTTP authorization headers may remain embedded in
workflow JSON or transit through workflow APIs.

### Objective

Create one secure credential abstraction shared by all integrations.

### Requirements

- Inventory every secret-bearing node field.
- Introduce an encrypted `Credential` or `Connection` model.
- Store credential references in node JSON rather than secret values.
- Redact all secrets in read/list APIs, logs, traces, and errors.
- Preserve an existing secret when a redaction marker is submitted.
- Add connection testing, replacement/rotation, ownership, and last-used metadata.
- Provide a safe migration path for existing workflows.
- Consider Google Secret Manager for deployment-owned secrets.
- Encrypt MCP OAuth client secrets. They are currently persisted as plaintext inside the
  `McpServer.oauthConfig` JSON object even though access/refresh tokens are encrypted.
- Treat arbitrary MCP `headers` as secret-bearing. The current read model returns stored headers
  directly, so shared server headers can be disclosed to users who can list/read that server.
- Redact token-like headers (`Authorization`, API-key variants, cookies) and migrate them into the
  credential store.
- Audit public workflow reads specifically: Jira tokens are redacted, but vector DB keys,
  embedding keys, and HTTP authorization headers do not receive equivalent protection.

### Acceptance criteria

- No supported workflow credential is returned through workflow APIs.
- New workflow JSON contains credential references instead of plaintext secrets.
- Existing Jira and vector workflows continue working after migration.
- MCP OAuth client secrets and secret headers are encrypted at rest and never returned in reads.
- Public workflow reads cannot disclose any inline integration credential.
- Tests cover create, read, update, redaction, replacement, authorization, and migration.

---

## P0-6: Add outbound HTTP request security controls

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `999c5ac`) — SSRF guard
> (`src/security/ssrf.py`: IP-literal + DNS-resolution + cloud-metadata-hostname blocking), a
> response-size cap, and URL redaction on the `http` node. See CHANGELOG.md.

### Problem

The HTTP node accepts a variable-substituted URL and sends the request from the backend without an
SSRF policy. A workflow author may therefore be able to target loopback, link-local, cloud metadata,
or private-network services reachable by the Cloud Run instance. URLs and the first part of error
responses are also persisted in failure details, which can leak query-string tokens or sensitive
upstream content.

### Objective

Make direct HTTP calls safe for a multi-user hosted product while retaining an explicit path for
administrators who intentionally need internal endpoints.

### Requirements

- Parse and validate the final substituted URL immediately before the request.
- Allow only `https` by default in production; make any `http` exception explicit and scoped.
- Block loopback, link-local, multicast, unspecified, and private address ranges by default.
- Block cloud metadata hostnames and addresses, including `169.254.169.254`.
- Resolve DNS and validate every resolved address to reduce DNS-rebinding risk.
- Revalidate every redirect target if redirects are enabled now or later.
- Add an administrator-controlled hostname allowlist for intentional internal integrations.
- Redact credentials and sensitive query parameters from execution results and errors.
- Bound response size before buffering it into memory or workflow state.
- Consider restricting outbound ports and content types.
- Document the security boundary and differences between self-hosted and hosted modes.

### Acceptance criteria

- Requests to localhost, private IPs, and metadata endpoints are rejected in hosted production.
- A permitted public HTTPS endpoint continues to work.
- DNS resolving to a blocked address is rejected.
- Redirects cannot bypass destination validation.
- URL credentials and token-like query parameters do not appear in persisted errors.
- Oversized responses fail with a clear bounded-size error.
- Tests cover IPv4, IPv6, encoded/alternate address forms, DNS resolution, and redirects.

---

## P1-1: Make email approvals scanner-safe

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `a8237ac`) — `GET /approvals/email/{token}`
> only validates and redirects to a confirmation page; the decision itself requires a `POST` from a
> real form submission. The in-app resume endpoint gained the same atomic conditional-transition
> guard. See CHANGELOG.md.

### Problem

The emailed approval URL performs a state-changing operation through GET. Email security scanners
and preview services may follow such links automatically. Atomic updates prevent two decisions
from winning, but they do not prevent the first scanner-opened link from making a decision.

### Objective

Require deliberate user confirmation before changing approval state.

### Requirements

- GET displays a public confirmation page and does not mutate state.
- A deliberate POST records the decision and resumes execution.
- Display workflow name, requested decision, approver, and expiration safely.
- Preserve signed-token verification, pause-instance binding, and atomic transition behavior.
- Apply an atomic waiting-to-running transition to in-app approval too. The current endpoint checks
  the status, creates an audit row, then performs an unconditional update; concurrent requests can
  both record decisions and schedule two resumptions.
- Protect the POST from cross-origin or CSRF-style submission.
- Handle expired, stale, invalid, and already-used links clearly.

### Acceptance criteria

- Link previews cannot approve or reject a workflow.
- Only confirmation POST changes execution state.
- Duplicate submissions cannot resume twice.
- Concurrent in-app approve/reject requests produce exactly one decision and one resumption.
- Stale links cannot decide a later pause.
- Tests cover scanners, races, expiration, and duplicates.

---

## P0-7: Protect workflow state and execution-input boundaries

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 — Jira credential precedence fixed (a
> `setdefault` bug let caller-supplied `variables.jira_api_token` silently override the node's own
> token; commit `7b5c828`), and `finalOutput`/`lastOutput` resolution changed from an `or`-fallback
> to a presence check so a legitimately falsy `finalOutput` isn't discarded (commit `d9b0933`). See
> CHANGELOG.md.

### Problem

The Start executor merges every caller-provided dictionary key into workflow variables, including
undeclared keys. Internal control values and integration configuration also live in the same flat
dictionary. The Jira executor lets existing `jira_domain`, `jira_email`, and `jira_api_token`
variables take precedence over node credentials. Together, these behaviors allow an external
caller to inject internal-looking state or redirect a Jira action to caller-controlled credentials
and domains, potentially sending workflow-generated content to an unexpected Jira tenant.

There is also a correctness bug in final-output and guardrail-input selection: boolean `or` is used
instead of checking whether a value is absent. Valid values such as `0`, `False`, `""`, `[]`, and
`{}` can therefore be discarded and replaced with a fallback.

### Objective

Separate untrusted inputs, workflow variables, internal engine state, and credentials while
preserving intentional workflow composition.

### Requirements

- In strict production mode, accept only Start-declared keys unless an explicit
  `allowAdditionalInputs` contract is enabled.
- Return structured validation errors for undeclared keys.
- Reserve engine-owned keys and prefixes such as `_pending_*`, `_approval_*`, retry counters,
  `finalOutput`, and credential namespaces.
- Prevent external inputs from supplying or overriding protected values.
- Move integration credentials out of general workflow variables or provide protected execution
  context unavailable to caller input.
- If Set State credential overrides remain supported, require explicit configuration and ensure
  external callers cannot activate them implicitly.
- Audit executors for `x or fallback` wherever falsey values are valid data; use presence or
  `None` checks instead.
- Specifically fix final execution output selection and Guardrails input selection.
- Define a compatibility strategy for workflows intentionally using undeclared inputs.

### Acceptance criteria

- External invocations cannot inject reserved engine variables.
- External invocations cannot override Jira credentials or redirect the configured Jira domain.
- Declared inputs continue working and retain their types.
- Undeclared inputs are rejected clearly in strict production mode.
- `finalOutput` values of `0`, `False`, `""`, `[]`, and `{}` are persisted exactly.
- Guardrails evaluate a deliberately falsey `lastOutput` without falling back to original input.
- Tests cover direct execution, external invocation, resume, loops, and compatibility mode.

### Likely areas

- `src/executors/start.py`
- `src/engine/state.py`
- `src/engine/langgraph_executor.py`
- `src/executors/jira.py`
- `src/executors/guardrails.py`
- `src/api/run.py`

---

## P0-8: Strengthen graph-shape and terminal-path validation

> **Status (2026-07-21): resolved (reachability mismatch closed).** Shipped 2026-07-12 (commit
> `6697ac3`) — `_check_reachability`'s validation-time BFS now filters visual-only node types the
> same way `build_graph`'s compile-time edge projection does. See CHANGELOG.md. The broader terminal-
> path / fan-out / multiple-End-node semantics this section also describes were not re-audited as
> part of that pass; treat those specifics as unverified rather than confirmed resolved.

### Problem

Graph validation computes reachability using every edge, including edges to and from visual-only
nodes such as Note and File Trigger. Compilation then removes those edges. A node can therefore be
declared reachable by validation but disconnected in the executable graph.

The validator also permits incoming edges to Start, outgoing edges from End, and executable paths
that never reach an End node. An End node with an outgoing edge is compiled with both an END edge
and the configured downstream edge, so the visual concept of a terminal node is not enforced.
Parallel branches can also converge on multiple End nodes whose `finalOutput` writes race through a
last-wins reducer, producing a potentially nondeterministic final output.

### Objective

Make the validated graph identical to the graph that will execute and guarantee explicit terminal
semantics.

### Requirements

- Build validation reachability from the same executable-edge projection used by compilation.
- Reject edges into Start and edges out of End.
- Require every reachable executable node to have at least one path to an End node, except bounded
  loop-body paths that eventually return to their loop controller.
- Reject executable paths that terminate implicitly without an End node.
- Define whether normal multi-edge fan-out is supported. If supported, document join/final-output
  semantics; if not, reject multiple unlabelled outgoing edges.
- Define deterministic final-output behavior for parallel branches and multiple End nodes.
- Detect cycles that do not pass through a bounded While controller.
- Validate edges involving visual-only nodes explicitly: either forbid them or treat them as visual
  decoration without letting them satisfy executable reachability.
- Reuse one normalized graph representation for validation and compilation to prevent drift.

### Acceptance criteria

- A path routed through a Note/File Trigger cannot make an executable node appear reachable.
- Start cannot have incoming edges and End cannot have outgoing edges.
- Every valid workflow has a deterministic executable terminal path.
- Unbounded non-While cycles are rejected before execution.
- Parallel final-output behavior is deterministic and tested.
- Tests cover disconnected visual paths, terminal misuse, cycles, fan-out/fan-in, and multiple Ends.

### Likely areas

- `src/engine/graph_builder.py`
- `src/engine/state.py`
- `src/executors/end.py`
- Graph-builder and integration tests

---

## P1-2: Move workflow execution to durable workers

> **Status (2026-07-21): resolved.** Shipped 2026-07-15 via Google Cloud Tasks + a Postgres
> claim/lease/sweep model — see ADR-0033 in `docs/decisions.md` and `docs/deferred-backlog.md` §P1-2
> for the full outcome and task-by-commit record.

### Problem

Executions are currently launched through request-bound background tasks or raw asyncio tasks.
Cloud Run instance termination, deployment, or CPU suspension can interrupt a run. A sweeper can
mark it failed but cannot finish the work.

### Objective

Make runs and approval resumptions durable across API and worker restarts.

### Required discovery

Write an ADR comparing Google Cloud Tasks, Pub/Sub, a Postgres-backed queue, and Redis-backed
workers. Prefer the smallest reliable option compatible with Cloud Run and LangGraph checkpoints.

### Requirements

- API creates an execution row and enqueues its ID.
- A worker atomically claims the execution.
- Add execution lease and heartbeat behavior.
- Recover expired leases with bounded retry/backoff.
- Route approval resume through the same durable mechanism.
- Support cancellation and dead-letter handling.
- Preserve idempotency across retry and recovery.
- Replace request-bound workflow execution.

### Acceptance criteria

- Killing a worker does not permanently strand a run.
- Two workers cannot execute the same lease concurrently.
- Approval resume survives API shutdown.
- External side effects are not duplicated during recovery.
- Integration tests simulate lease expiration and worker termination.

---

## P1-3: Add idempotency for side-effecting nodes

> **Status (2026-07-21): resolved at the execution-API layer.** Shipped 2026-07-12 (commit `cf8f84c`)
> — optional `idempotencyKey` on `POST /executions` and `POST /api/run/{slug}`, scoped per workflow
> and backed by a DB-level unique constraint on `(workflow_id, idempotency_key)`, so a retried
> request returns the original execution instead of duplicating side effects. See CHANGELOG.md. The
> more granular per-node-operation idempotency this section also describes (stable operation
> identity per node/loop-iteration, provider idempotency keys) was not separately implemented —
> treat that scope as still open.

### Objective

Prevent duplicate Jira issues, emails, comments, and HTTP mutations during retry or resume.

### Requirements

- Generate a stable operation identity from execution ID, node ID, loop iteration, and operation
  index.
- Persist operation state before external calls.
- Reuse completed results on retry.
- Use provider idempotency keys where supported.
- Define behavior for uncertain outcomes such as a timeout after remote acceptance.

### Acceptance criteria

- Retrying a completed Jira create does not create a duplicate issue.
- Retrying an email node does not send the same message twice.
- Separate loop iterations remain distinct.
- Operation history is visible in execution diagnostics.

---

## P1-4: Replace process-local events and rate limits

> **Status (2026-07-21): resolved.** Shipped 2026-07-15 — events moved to a durable, sequence-numbered
> `execution_events` table with Postgres `LISTEN/NOTIFY` as a wake-up signal; rate limiting moved to
> a Postgres-backed atomic bucket table. See ADR-0033 in `docs/decisions.md` and
> `docs/deferred-backlog.md` §P1-4.

### Problem

The execution event bus and rate-limit buckets are process-local, which makes their behavior
incorrect when multiple backend instances serve traffic.

### Objective

Make real-time events and rate limiting consistent across instances.

### Requirements

- Use a shared event mechanism such as Redis, Postgres `LISTEN/NOTIFY`, or Pub/Sub.
- Use an atomic shared store for rate limiting.
- Preserve the current client protocol where practical.
- Add event sequence numbers and reconnect cursors.
- Retain enough history to recover missed terminal events.
- Add bounded retention and cleanup.

### Acceptance criteria

- A client on one API instance receives events emitted by another worker.
- Restarting an instance does not reset rate limits.
- Reconnecting clients recover terminal status.
- Multi-instance tests verify the behavior.

---

## P1-5: Improve execution trace usability

> **Status (2026-07-21): partially resolved.** Shipped 2026-07-12 (commit `73473ee`) — every node's
> `node_results` entry and `node_completed`/`node_failed` WebSocket event now carry
> `startedAt`/`completedAt`/`durationMs`, added once in `wrap_executor_with_events`. See CHANGELOG.md.
> The rest of this section's requirements (retry count, LLM token usage/cost, `completed_with_warnings`
> status, copyable diagnostic summary) were not part of that change — treat those as still open.

### Objective

Make it obvious what every node attempted, accomplished, skipped, or failed to do.

### Requirements

Show, with secret sanitization:

- Original configured input.
- Substituted runtime input.
- Start/end time and duration.
- Status including `completed_with_warnings`, `skipped`, and `cancelled` where relevant.
- Tool calls and structured outcomes.
- Created external resource identifiers.
- Retry count.
- LLM token usage and estimated cost where available.
- Warnings and a copyable diagnostic summary.

### Acceptance criteria

- Users can determine whether Jira was actually called.
- Secrets remain masked.
- Large outputs are collapsed or truncated safely.
- Persisted terminal state remains usable after real-time disconnection.

---

## P1-6: Correct execution API status, sizing, deletion, and cancellation semantics

> **Status (2026-07-21): resolved.** Shipped 2026-07-12 (commit `b3e31a3`) — sync `POST /api/run/{slug}`
> no longer hard-codes `running` for a paused approval after timeout; both execution-input size
> checks now measure true UTF-8 byte length; `DELETE /executions/{id}` and
> `POST /executions/delete-bulk` reject/skip active executions; added
> `POST /executions/{id}/cancel`. See CHANGELOG.md.

### Problem

Several execution API behaviors are internally inconsistent:

- Sync external invocation polls only terminal statuses. A workflow paused at approval waits until
  timeout and then receives a response hard-coded to `running`, even though the persisted status is
  `waiting_approval`.
- Execution input limits are documented and reported as bytes but use Python string length after
  JSON serialization, undercounting non-ASCII UTF-8 payloads.
- Execution deletion endpoints do not reject or cancel active `running` or `waiting_approval`
  executions before deleting their rows and checkpoints. In-flight background work can continue
  external side effects and later fail persistence against a deleted execution.
- The public status vocabulary documents `canceled`, while worker shutdown is persisted as
  `failed`; there is no coherent user cancellation operation.

### Objective

Give callers truthful status and make active execution lifecycle operations safe and explicit.

### Requirements

- Treat `waiting_approval` as an immediate meaningful sync response rather than polling until
  timeout, or return the current persisted status at timeout.
- Never hard-code `running` when the database says otherwise.
- Measure serialized input using UTF-8 byte length in both execution endpoints.
- Define active-run deletion behavior: reject with 409, or atomically cancel and wait for worker
  acknowledgement before cleanup.
- Add a first-class cancellation endpoint and persisted cancellation reason/time if cancellation
  is supported.
- Ensure a canceled/deleted execution cannot continue producing side effects.
- Use one status enum/contract across database, API models, WebSocket snapshots, frontend, sweeper,
  and documentation.
- Make checkpoint and execution deletion transactional where the persistence stack permits it.

### Acceptance criteria

- Sync invocation returns `waiting_approval` promptly for approval workflows.
- Non-ASCII input limits enforce actual UTF-8 bytes consistently.
- Deleting or canceling an active run cannot leave its worker executing unnoticed.
- WebSocket, REST, database, and UI agree on canceled/cancelled spelling and meaning.
- Tests cover approval pauses, timeout races, Unicode payloads, active deletion, and cancellation.

### Likely areas

- `src/api/run.py`
- `src/api/executions.py`
- `src/engine/langgraph_executor.py`
- `src/api/events_ws.py`
- Execution API and integration tests

---

## P2-1: Add dry-run execution mode

> **Status (2026-07-21): not started — deferred pending a product decision.** See
> `docs/deferred-backlog.md` §P2-1 for the specific control-flow-semantics question that needs
> answering before implementation starts.

### Objective

Preview external actions without performing mutations.

### Requirements

- Add `live` and `dry_run` execution modes.
- Side-effect nodes return structured previews in dry-run mode.
- Read-only actions execute only when explicitly permitted.
- Clearly label dry-run state in APIs and UI.
- Prevent dry-run notifications from implying real actions occurred.

### Acceptance criteria

- Jira previews proposed issues without creating them.
- Email previews recipients, subject, and sanitized body without sending.
- HTTP mutations show the proposed request without transmitting it.
- Dry-run records cannot be confused with live executions.

---

## P2-2: Add reusable connection management

> **Status (2026-07-21): not started — deferred, and sequenced behind P0-5.** See
> `docs/deferred-backlog.md` §P2-2. P0-5's `Credential`/`Connection` model decision needs to land
> first — a Connections page needs something to manage.

### Objective

Improve credential reuse, rotation, testing, and setup usability.

### Requirements

- Add an admin/user Connections page.
- Support reusable Jira, vector DB, email, and HTTP authentication connections.
- Show health status and last test time.
- Support scoped sharing with authorization checks.
- Show dependent workflows before rotation or deletion.

### Acceptance criteria

- Users select a Jira connection instead of repeatedly pasting credentials.
- Rotation updates dependent workflows without editing each node.
- Deleting an in-use connection requires explicit confirmation.

---

## P2-3: Split oversized modules along domain boundaries

> **Status (2026-07-21): not started — deliberately deferred.** See `docs/deferred-backlog.md` §P2-3:
> `src/engine/workflow.py` and related engine files have been under continuous concurrent editing by
> other feature work, so a large structural refactor right now risks merge-conflict churn for no
> functional benefit. Revisit once that work settles.

### Objective

Reduce maintenance risk without changing external behavior.

### Suggested targets

- `src/engine/workflow.py`
- `src/api/workflows.py`
- `src/tools/providers/jira.py`
- `frontend/components/composer/canvas/workflow-canvas.tsx`
- `frontend/components/composer/canvas/designer-execution-panel.tsx`

### Requirements

- Separate models, validation, credential handling, persistence, execution, and presentation.
- Preserve API contracts.
- Avoid formatting churn and speculative abstractions.
- Do not introduce circular imports.

### Acceptance criteria

- Existing tests remain green.
- No intended user-facing behavior changes.
- New modules have focused, documented responsibilities.

---

## P2-4: Improve dependency and build reproducibility

> **Status (2026-07-21): not started — deferred.** See `docs/deferred-backlog.md` §P2-4. Note that
> this section's recommended upgrade order lists "replace or contain Prisma Client Python risk" as
> step 2 — that step is resolved by ADR-0031 (stay on Prisma Python; see P3-1 below), which unblocks
> the rest of the sequence without a persistence-layer migration.

### Objective

Make local, CI, and production dependency graphs deterministic and maintainable.

### Version assessment (audited 2026-07-11)

Resolved versions observed in the local environment:

| Component | Resolved version | Assessment |
|---|---:|---|
| LangGraph | 1.1.8 | Good choice and on the active v1 LTS line; update within v1 after regression tests. Latest observed stable was 1.2.8. |
| LangChain | 1.2.15 | Supported v1 line but behind the latest observed 1.3.x line. Upgrade only as a tested LangChain/LangGraph/provider bundle. |
| LangChain Core | 1.3.0 | Modern; must remain compatible with the selected LangChain/provider packages. |
| FastAPI | 0.136.0 | Modern and close to current; latest observed was 0.139.0. Low urgency. |
| Pydantic | 2.13.2 | Current stable family; patch update to 2.13.4 is low risk after tests. |
| Prisma Client Python | 0.15.0 | Highest backend stack risk: upstream repository was archived in April 2025 and is read-only. |
| HTTPX | 0.28.1 | Modern; retain. |
| Uvicorn | 0.44.0 | Modern; retain with routine patch updates. |
| Next.js | 14.2.35 | Unsupported under the current Next.js support policy. Plan migration to a supported LTS major. |
| React / React DOM | 18.3.1 | Deliberate bridge release but behind stable React 19. Upgrade together with Next.js. |
| NextAuth | 5.0.0-beta.31 | Pre-release authentication dependency in production; validate current Auth.js path and remove beta dependence if possible. |
| React Flow | 11.11.4 (`reactflow`) | Old package line. React Flow 12 uses `@xyflow/react`; migrate with dedicated canvas regression tests. |
| TanStack React Query | 5.99.2 | Current major and appropriate. |

### Architectural opinion

- Keep LangGraph. Composer uses exactly the capabilities LangGraph is designed for: durable state,
  checkpointing, interrupts, resume, streaming, and custom deterministic/agentic graphs.
- Do not replace LangGraph with a high-level agent framework. Composer is itself the orchestration
  product and needs low-level graph control.
- Reduce dependence on ad-hoc LangChain agent loops over time by extracting one tested internal
  tool-loop abstraction or adopting stable LangChain v1 agent middleware where it fits. Keep
  Composer's workflow graph in LangGraph.
- Prioritize persistence migration and frontend supported-version upgrades before chasing every
  minor Python release.
- Treat the LangGraph/LangChain/provider family as one compatibility set. Do not use unconstrained
  lower bounds to assemble arbitrary future combinations.

### Recommended upgrade order

1. Lock and test the current dependency graph; add automated compatibility/update checks.
2. Replace or contain Prisma Client Python risk (ADR and incremental SQLAlchemy/Alembic plan).
3. Upgrade Next.js 14 + React 18 + NextAuth beta as one planned frontend platform project.
4. Migrate `reactflow` 11 to `@xyflow/react` 12 with persisted-node and canvas interaction tests.
5. Update LangGraph 1.1.8 to the current v1 LTS minor and align LangChain/provider packages.
6. Apply routine FastAPI/Pydantic patch/minor updates after the higher-risk platform work.

### Requirements

- Audit broad lower-bound-only Python dependencies.
- Replace broad independent LangChain/LangGraph lower bounds with a tested compatibility policy or
  constraints file; recent upstream histories include yanked regression releases, so blind latest
  resolution is not appropriate for production.
- Confirm Docker and CI install from the same lockfile.
- Add automated, isolated dependency-update pull requests.
- Separate production and development dependencies clearly.
- Document supported Python and Node versions.
- Plan framework upgrades through compatibility tests rather than opportunistic bulk updates.

### Acceptance criteria

- Clean local, CI, and production builds use the same locked dependency graph.
- Dependency upgrades are isolated and testable.
- Supported runtime versions are explicit.

---

## P3-1: Evaluate the long-term Python persistence stack

> **Status (2026-07-21): resolved.** ADR-0031 (`docs/decisions.md`) decided to stay on Prisma Python,
> validated by a proof-of-concept (`scripts/poc_persistence_row_lock.py`) confirming Prisma's
> raw-SQL escape hatch supports the `SELECT ... FOR UPDATE SKIP LOCKED` row-claiming pattern P1-2
> needed. See `docs/deferred-backlog.md` §P3-1 for concrete trigger conditions for revisiting.

### Objective

Determine whether Prisma Python remains appropriate or whether SQLAlchemy 2 and Alembic offer a
safer long-term path.

### Constraint

This is an ADR and proof-of-concept task first. It is not authorization for a repository-wide ORM
rewrite.

### Required analysis

- Prisma Python maintenance and ecosystem risk.
- Async database support.
- Type safety and schema generation.
- Transactions, row locking, leases, and worker claims.
- Migration tooling.
- LangGraph checkpoint integration.
- Incremental migration strategy.
- Regression risk and operational cost.

### Acceptance criteria

- An ADR gives a clear evidence-based recommendation.
- A small proof of concept validates the hardest transaction/query patterns.
- No production migration begins without explicit review.

---

## Prioritization response requested from Claude Code

After reading this document and inspecting the repository, respond with:

1. Findings that remain valid, with supporting file references.
2. Findings that are already resolved or need revision.
3. A dependency graph between backlog items.
4. A ranked top five with impact, effort, risk, and rationale.
5. The smallest recommended first implementation slice.
6. Files likely to change for that slice.
7. A test and rollback plan.
8. Any decisions requiring user input.

Do not edit code until the user approves the proposed first slice.

## Copy-paste prompt for Claude Code

```text
Read docs/claude-improvement-backlog.md and inspect the current Composer repository.
Follow the document's working instructions and preserve all existing worktree changes.

Validate the backlog against the code rather than accepting it blindly. Report which findings
remain valid, which are already addressed, and which need revision. Then propose a dependency-aware
top-five priority list with impact, effort, risk, and rationale. Recommend the smallest first
implementation slice, including likely files, tests, verification commands, and rollback plan.

Do not modify code yet. Wait for my approval of the first implementation slice.
```
