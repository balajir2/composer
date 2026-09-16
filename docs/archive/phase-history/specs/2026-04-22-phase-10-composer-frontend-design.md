# Phase 10 — Composer Frontend + Enterprise UX: Design

**Status.** Draft 2026-04-22.
**Related.** Phase 7a auth (ADR-0014/0015), Phase 7b workflow CRUD, Phase 8 security (ADR-0021), Phase 9 cutover (ADR-0022), ADR-0023 (this phase — frontend + enterprise-UX policy).
**Supersedes.** Phase 10 section of [docs/design/2026-04-20-composer-python-port-design.md](../../design/2026-04-20-composer-python-port-design.md) which described a straight fork of OAB's frontend. This spec replaces that with a fresh enterprise-UX build; OAB remains a behavioral reference only.

---

## 1. Goal

Ship Composer as a usable enterprise product with a fresh UX built for three distinct audiences — **Designer**, **End User**, **Admin** — atop the Phase 0–9 backend. Users log in via Azure SSO or standalone email/password; Designers build workflows; End Users run published workflows via UI or external API; Admins manage users and publish tools/MCPs to a shared catalog. Every published workflow is callable from outside the app via a stable, API-key-authenticated URL.

**Concretely:**
- Fresh Next.js 14 frontend under `composer/frontend/` (monorepo, not a separate repo).
- Next.js App Router + TypeScript + Tailwind CSS + shadcn/ui (Radix primitives).
- NextAuth.js with Azure AD provider; standalone email/password path preserved from Phase 7a.
- Three role-aware route trees in one app: `/designer/*`, `/runs/*`, `/admin/*`.
- Backend additions: `Workflow.isProduction` + `Workflow.externalSlug` + `ApiKey` model + `POST /api/run/{slug}` + `User.passwordHash` made nullable.
- External-invoke via `Authorization: Bearer ck_<key>` against `POST /api/run/{slug}` — async by default, opt-in sync with timeout cap.
- Tool/MCP publishing catalog: admins flip `isShared` on MCP rows and toggle built-in tool providers for the deployment; designers see a unified "Tools" palette.

## 2. Non-goals

- **No OAB visual port.** OAB's UI is not reproduced. UX is re-designed for enterprise audiences; OAB is behavioral reference only.
- **No per-workflow version snapshots.** Editing a production workflow updates the latest; no historical versioning in this phase. (If a workflow is being actively invoked externally, designers should test in a draft copy — no tooling enforces this yet.)
- **No group-based access.** Phase 10 ships role-based access only (public = any authenticated user + any API key; private = owner + admin). Group-based ACL is a future phase if internal scale demands it.
- **No Convex → Composer data bridge in the frontend.** Phase 9's one-shot `composer migrate` already moved OAB data; the frontend only talks to Composer's REST/WS.
- **No multi-tenant segmentation.** Single-tenant (internal). Tenant-scoping is a Phase 11+ concern if Composer is productized externally.
- **No self-serve password reset.** Users who forget their password contact an admin (DB `UPDATE`). `/auth/forgot-password` flow deferred.
- **No API-key rotation automation.** Users create keys, revoke them, and create new ones manually. No "rotate key" convenience endpoint.
- **No branded white-labeling.** Enterprise look but Composer-default theming (with CSS-variable hooks so custom theming can be applied later as a small PR).
- **No offline / PWA.** Frontend assumes connectivity; online-only.
- **No mobile-first.** Desktop-first, mobile-tolerable for the End User UI only; Designer and Admin are desktop-only.

## 3. Sub-phase structure

Phase 10 is **six sub-phases**, risk-first order. Each sub-phase is shippable on its own; later sub-phases depend on earlier ones.

| # | Sub-phase | Ships |
|---|---|---|
| **10a** | Backend extensions | `Workflow.isProduction` + `Workflow.externalSlug` + `ApiKey` model + `POST /api/run/{slug}` + `User.passwordHash` nullable + `POST /auth/sso-exchange` |
| **10b** | Frontend scaffold | `composer/frontend/` Next.js app + Tailwind + shadcn/ui + NextAuth (Azure AD + Credentials providers) + OpenAPI-generated Composer client + role-aware layout |
| **10c** | End User UI | `/runs/*` — list workflows you can run, input form, submit, live progress (WebSocket), result view; API key management page |
| **10d** | Admin UI | `/admin/*` — user list + promote/demote role; MCP server list + `isShared` toggle + OAuth setup wizard; Built-in tool provider toggles; LLM keys management (Phase 9e endpoints); Workflow override (reassign owner, force-publish) |
| **10e** | Designer UI | `/designer/*` — workflow list + editor (React Flow canvas); node property panels for all 15 node types; unified tool/MCP palette pulled from catalog; publish flow (mark as production + pick externalSlug) |
| **10f** | Enterprise polish + Playwright | Accessibility pass (WCAG AA via Radix primitives' built-ins); design system consistency pass; Playwright end-to-end tests (login, create workflow, publish, run via UI, run via API key); visual QA; load testing |

**Rationale:** 10a is backend-only so the UI can be built against stable API contracts. 10b is the thinnest possible frontend — auth + role shell — so we can iterate. 10c ships the first user-facing value (running published workflows) in ~3–4 weeks from phase start. 10d unlocks admin tooling so the catalog can be populated. 10e is the most complex UI work (canvas). 10f polishes the whole thing.

---

## 4. Sub-phase 10a — Backend extensions

### 4.1 Prisma schema changes

Two model edits + one new model. All via a single Prisma migration named `phase10a_production_workflows_and_api_keys`.

```prisma
model Workflow {
  // ... existing fields ...
  isProduction    Boolean  @default(false) @map("is_production")
  externalSlug    String?  @unique         @map("external_slug")
  // ...
}

model User {
  id            String    @id @default(cuid())
  email         String    @unique
  passwordHash  String?   @map("password_hash")  // NEWLY NULLABLE for SSO users
  displayName   String?   @map("display_name")
  role          UserRole  @default(member)
  createdAt     DateTime  @default(now()) @map("created_at")
  updatedAt     DateTime  @updatedAt      @map("updated_at")

  apiKeys       ApiKey[]

  @@map("users")
  @@index([email])
}

model ApiKey {
  id            String    @id @default(cuid())
  userId        String    @map("user_id")
  label         String    // user-visible name, e.g. "CI bot key"
  keyHash       String    @unique @map("key_hash")  // bcrypt hash of the full key
  keyPrefix     String    @map("key_prefix")        // first 12 chars for display, e.g. "ck_abc123de"
  createdAt     DateTime  @default(now()) @map("created_at")
  lastUsedAt    DateTime? @map("last_used_at")
  expiresAt     DateTime? @map("expires_at")
  revokedAt     DateTime? @map("revoked_at")

  user          User      @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@map("api_keys")
  @@index([userId])
  @@index([keyPrefix])
}
```

Notes:
- `externalSlug` is globally unique and must match `^[a-z0-9][a-z0-9-]{1,63}$` (Pydantic validator; enforced at publish time, not schema-level).
- `ApiKey.keyHash` is bcrypt-hashed (same `bcrypt_rounds` setting as password storage). Plaintext returned only once at creation.
- `User.passwordHash` nullable handles SSO-provisioned users who never set a password.

### 4.2 Publishing a workflow as production

`PUT /workflows/{id}` body gains two optional fields: `isProduction` and `externalSlug`.

Authz:
- Workflow owner can flip own workflow's `isProduction`. Must provide `externalSlug` when setting `isProduction = true`.
- Admin can flip on any workflow (Phase 9f publish bypass already covers PUT).
- When `isProduction = true`, the slug is reserved globally; 409 on conflict.
- When `isProduction` is set back to `false`, the slug is released (NULL).

### 4.3 External invoke endpoint

`POST /api/run/{slug}`

- **Auth:** `Authorization: Bearer ck_<key>` header. Server strips `ck_` prefix, matches `ApiKey` by `keyPrefix`, bcrypt-verifies the full value, checks `revokedAt IS NULL` and `expiresAt > now`.
- **Lookup:** find `Workflow` by `externalSlug`. 404 if not found.
- **Authz:**
  - Workflow `isProduction` must be `true` (else 404 — info-leak tight).
  - If workflow `isPublic = true`: any valid API key works.
  - If `isPublic = false`: API key's `userId` must equal workflow's `userId`, OR API key's user is admin.
- **Body:** `{"input": <JSON>, "sync": bool = false, "timeoutSeconds": int = 60}` — `input` matches current `POST /executions` body. Size cap from Phase 8 still applies (1 MB default).
- **Default async response (202):** `{"execution_id": "clm7...", "workflow_id": "clm7...", "status": "running", "stream_url": "wss://host/executions/clm7.../ws"}`. Caller polls `GET /executions/{id}` or subscribes to WebSocket.
- **Sync response (200 on complete, 202 on timeout):** wait up to `timeoutSeconds` (max 300); if execution reaches terminal status, return `{"execution_id": "...", "status": "completed", "output": {...}}`; if still running at timeout, fall back to async shape above.
- **Execution.userId is set to the API key's owning user.** Audit trail remains clean.
- **Rate limit:** new bucket `api_run_per_minute` (default 60/min keyed by API key).

### 4.4 Azure SSO exchange endpoint

NextAuth on the frontend validates an Azure AD JWT, then calls Composer to mint a Composer JWT.

`POST /auth/sso-exchange` body: `{"azure_token": "<Azure-issued JWT>"}`

Server steps:
1. Validate the Azure token against Azure AD's JWKS (configurable tenant ID, expected `aud`).
2. Extract `email` claim.
3. Look up `User` by email (lowercased). If not found → auto-create with `passwordHash = NULL`, `role = member`, `displayName = <name claim>`.
4. Issue standard Composer access + refresh tokens (same `create_access_token` / `create_refresh_token` as Phase 7a standalone login).

This endpoint is the only new auth surface. Standalone `/auth/register` and `/auth/login` stay unchanged.

### 4.5 API key CRUD

All under `/api-keys/*`, scoped to the authenticated user.

- `POST /api-keys` body `{"label": str, "expires_at": ISO8601?}` → returns `{id, label, keyPrefix, key, createdAt, expiresAt}`. **`key` is the full plaintext, returned once; the server only stores the hash.**
- `GET /api-keys` → list the caller's keys (prefix + label + lastUsedAt, no plaintext).
- `DELETE /api-keys/{id}` → sets `revokedAt = now()`. Soft-delete so audit trail survives.
- Admin can list + revoke any user's keys via `/admin/api-keys` (see §10d).

### 4.6 Settings additions

```python
rate_limit_api_run_per_minute: int = 60

# SSO
sso_azure_ad_tenant_id: str = ""
sso_azure_ad_expected_audience: str = ""
sso_enabled: bool = False  # must be True AND both above must be set for /auth/sso-exchange to accept
```

`/auth/sso-exchange` returns 400 if `sso_enabled` is False.

### 4.7 Tests

- Unit: `ApiKey` CRUD (create returns plaintext once; list omits it; revoke soft-deletes).
- Unit: `POST /api/run/{slug}` happy paths (authenticated + authorized + production + public/private matrix).
- Unit: `POST /auth/sso-exchange` happy (Azure token valid → user created/returned + Composer JWT minted) and sad paths (invalid token → 401; SSO disabled → 400).
- Unit: `PUT /workflows/{id}` publish — unique slug enforcement; invalid slug format; unpublish releases slug.
- Integration (real Neon): two users, one publishes a workflow; external API-key invoke succeeds; another user's API key fails on private workflow.

---

## 5. Sub-phase 10b — Frontend scaffold

### 5.1 Directory layout

```
composer/
├── frontend/
│   ├── app/                    # Next.js App Router
│   │   ├── layout.tsx          # Root layout (fonts, globals)
│   │   ├── page.tsx            # Landing → redirects to role home
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── layout.tsx      # Public layout
│   │   ├── designer/
│   │   │   ├── layout.tsx      # Designer-authz guard + nav
│   │   │   └── page.tsx        # Placeholder in 10b; filled in 10e
│   │   ├── runs/
│   │   │   ├── layout.tsx      # End-user-authz guard + nav
│   │   │   └── page.tsx        # Placeholder in 10b; filled in 10c
│   │   └── admin/
│   │       ├── layout.tsx      # Admin-authz guard + nav
│   │       └── page.tsx        # Placeholder in 10b; filled in 10d
│   ├── components/
│   │   ├── ui/                 # shadcn/ui-generated primitives
│   │   └── composer/           # Composer-specific components
│   ├── lib/
│   │   ├── api/                # OpenAPI-generated TS client
│   │   ├── auth.ts             # NextAuth config + Composer JWT exchange
│   │   └── ws.ts               # WebSocket client
│   ├── public/                 # Static assets
│   ├── styles/
│   │   └── globals.css         # Tailwind + design tokens
│   ├── next.config.js
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── .env.example
├── src/                        # Existing Python backend
├── tests/                      # Existing Python tests
└── pyproject.toml
```

### 5.2 Stack

- **Next.js 14+** App Router (RSC + server actions where helpful).
- **TypeScript** strict mode.
- **Tailwind CSS 3+** — utility classes; design tokens as CSS variables for future custom theming.
- **shadcn/ui** — Radix primitives (Dialog, DropdownMenu, Sheet, Toast, Table, Form, Button, Input, Select, Tabs, Tooltip, Sonner toasts). Copied into `components/ui/` via `npx shadcn init`.
- **NextAuth.js v5 (Auth.js)** — session management. Two providers:
  - `AzureAD` — Azure tenant configurable via env (`AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`, `AZURE_AD_CLIENT_SECRET`).
  - `Credentials` — delegates to Composer's `/auth/login`.
- **OpenAPI client** via `openapi-typescript` + a thin hand-written wrapper that handles auth headers, error formatting, and token refresh. Regenerated from `http://localhost:8000/openapi.json` via `npm run generate-client`.
- **WebSocket client** — thin wrapper over native `WebSocket` API, handles `["bearer", token]` subprotocol + 4401/4403/4404 close codes.
- **State management:** React Query (`@tanstack/react-query`) for server state. No Redux, no Zustand beyond what React Query offers. Server-rendered pages get prefetched queries via `QueryClient.prefetchQuery` in the RSC layer.
- **Forms:** `react-hook-form` + `zod` for schema validation (shadcn/ui form components integrate both).

### 5.3 Auth flow

1. User hits `/login` → picks "Sign in with Azure" or fills email/password form.
2. **Azure path:** NextAuth redirects to Azure AD → user approves → returns with Azure JWT. NextAuth runs a `jwt` callback that calls `POST /auth/sso-exchange` with the Azure token, receives Composer access + refresh tokens, stashes them in the NextAuth session (encrypted cookie).
3. **Credentials path:** NextAuth's `authorize` callback calls `POST /auth/login`, receives Composer tokens, stashes in session.
4. Every Composer API call reads the access token from the session, includes `Authorization: Bearer <composer_access>`.
5. 401 from Composer → NextAuth calls `POST /auth/refresh` with refresh token; if that fails, user is logged out.
6. A single NextAuth session carries the user's role (from the Composer `/auth/me` endpoint called once after login); layout-level guards check role against route prefix.

### 5.4 Role-aware routing

Each role's layout (`app/{designer,runs,admin}/layout.tsx`) is a server component that:
1. Reads NextAuth session.
2. If no session → redirect to `/login?returnTo=<current>`.
3. If role doesn't permit this route tree → redirect to user's role home (`/runs` for member-only; `/designer` for member+designer; `/admin` for admin).

For 10b all three layouts render a stub page confirming the role-gated access works. 10c/d/e fill in the real pages.

### 5.5 Tests

- Jest/Vitest unit tests for auth callback logic (token refresh edge cases).
- Playwright smoke: login via credentials → land on `/runs`; login as admin → `/admin` accessible; member-role user hitting `/admin` is redirected.

---

## 6. Sub-phase 10c — End User UI

### 6.1 Pages under `/runs`

- **`/runs` (home)** — list of workflows the user can run (production-mode + (public or owned)). Each row: name, description, last-used-by-me timestamp, "Run" button.
- **`/runs/{workflowId}`** — workflow details + input form. The form is driven by the workflow's Start node schema (file inputs, text, JSON, etc.). Submit → navigate to `/runs/{workflowId}/executions/{executionId}`.
- **`/runs/{workflowId}/executions/{executionId}`** — live progress view. Status badge (running / waiting_approval / completed / failed), per-node progress list (node_started → node_completed), output view when done. Backed by WebSocket `/executions/{id}/ws`.
- **`/runs/history`** — list of caller's executions with filter (workflow, status, date).
- **`/runs/api-keys`** — manage own API keys (create, list, revoke). Creating a key shows the full plaintext once in a modal with a copy button.

### 6.2 Components

- `WorkflowCard` — stylized card for the workflows list.
- `WorkflowInputForm` — react-hook-form that renders fields derived from the workflow's Start node (same node schema the Designer UI uses).
- `ExecutionProgress` — subscribes to WS, shows per-node timeline.
- `ExecutionResult` — renders `workflow_completed.output` as a read-only JSON tree (`react-json-view`-style) or formatted output if the workflow's End node declares a rendering hint.
- `ApiKeyCreateDialog` — shows plaintext in a modal after create; toast on copy.
- `ApproveDialog` — when a workflow pauses on a user-approval node and the caller is the designated approver, show approve/reject with optional note. Calls `POST /executions/{id}/resume`.

### 6.3 Tests

- Unit: `WorkflowInputForm` renders inputs from a mock Start node schema.
- Unit: `ExecutionProgress` handles `node_started → node_completed → workflow_completed` sequence.
- Playwright (deferred to 10f): full happy path — log in, pick workflow, fill form, run, watch progress, see output.

---

## 7. Sub-phase 10d — Admin UI

### 7.1 Pages under `/admin`

- **`/admin` (home)** — dashboard with counters (total users, active workflows, production workflows, executions today).
- **`/admin/users`** — user list with search by email; actions: promote/demote role, view API keys, revoke API keys.
- **`/admin/mcp-servers`** — list all MCP servers (Phase 3b), toggle `isShared` per-row, OAuth setup wizard (guides creating a new MCP server with OAuth config).
- **`/admin/tools`** — built-in tool provider toggles (Tavily/Serper/Firecrawl/Browserless). Reads/writes a new `deployment_settings` table (see §7.3).
- **`/admin/llm-keys`** — Phase 9e admin CRUD UI for LLM keys (existing endpoints; this page wraps them).
- **`/admin/workflows`** — all workflows across all users; actions: reassign owner (Phase 9f `PATCH /workflows/{id}/owner`), force-publish, force-unpublish.

### 7.2 Backend additions

- `POST /admin/users/{id}/role` with body `{"role": "admin" | "member"}` — admin-only. (Phase 9 left admin-promotion as SQL-only; this endpoint brings it into the UI.)
- `POST /admin/users/{id}/api-keys/{keyId}/revoke` — admin force-revoke.

### 7.3 Built-in tool provider toggles

New Prisma model:

```prisma
model DeploymentSetting {
  key      String @id          // "tool.tavily.enabled", "tool.firecrawl.enabled", etc.
  value    String              // "true" / "false"
  updatedAt DateTime @updatedAt @map("updated_at")

  @@map("deployment_settings")
}
```

Helper `src/config.py` reads these at settings init and merges over the static default (all enabled). Admin UI reads/writes via `GET/PUT /admin/deployment-settings/{key}`.

### 7.4 Tests

- Unit: role-promote endpoint — admin can, member cannot.
- Unit: deployment-setting upsert + read-merge logic.
- Playwright (deferred to 10f): admin promotes a user; the promoted user sees `/admin` accessible.

---

## 8. Sub-phase 10e — Designer UI

### 8.1 Pages under `/designer`

- **`/designer` (home)** — workflow list (owned + public templates), actions: new, duplicate, open, delete.
- **`/designer/{workflowId}`** — canvas editor.
- **`/designer/{workflowId}/settings`** — metadata (name, description, category, tags, isPublic, isProduction, externalSlug).

### 8.2 Canvas

- **React Flow** (Phase 10e) — same library OAB uses. Custom node types for all 15 node kinds. Edge types for conditional branches.
- **Unified Tools palette** (left sidebar) — tabbed or filterable list: "Agents", "Tools" (MCP-shared + enabled built-ins), "Control Flow", "Data", "Human". Drag from palette → drops a pre-configured node on canvas.
- **Property panel** (right sidebar) — node-specific editor. For Agent: model picker (LLM provider + model), tool bindings (which Tools palette items this Agent can call), system prompt. For HTTP: URL, method, headers, body. Etc.
- **Variable substitution helper** — when an input cell accepts variables (`{{input.foo}}`, `{{nodeResults.X.output}}`), show an autocomplete picker over the workflow's state graph.
- **Workflow-level settings** — top-bar dropdown: save, publish (opens dialog that asks for externalSlug), run-as-draft (triggers an ephemeral execution for testing).

### 8.3 Publish dialog

When user clicks "Publish":
- Checks that workflow is valid (no orphan nodes, etc.) — calls existing Composer `POST /workflows` validator.
- Shows a form: `externalSlug` (pre-filled with `slugify(workflow.name)`; editable) + a preview of the external URL.
- On submit: `PUT /workflows/{id}` with `isProduction=true` + `externalSlug`. On 409 (slug conflict): show error, ask to pick a different slug.
- On success: close dialog, show toast with the external URL + copy button. Navigate to `/designer/{id}/settings`.

### 8.4 Node property panels

Port OAB's node panels as behavioral reference, but re-implement in shadcn/ui form components. Each of the 15 node types gets a `components/composer/node-panels/<type>.tsx` file.

### 8.5 Tests

- Unit: property panel form validation (agent node requires a model).
- Unit: slug pre-fill / edit / conflict flow.
- Playwright (deferred to 10f): create workflow, add nodes, connect edges, save, publish, verify external URL works.

---

## 9. Sub-phase 10f — Enterprise polish + Playwright

### 9.1 Accessibility

- Every interactive component lands on Radix primitives (which ship WCAG-compliant keyboard nav + aria-* attributes).
- Run `axe-core` against every page in Playwright; fail CI on violations with severity ≥ moderate.
- Focus rings must be visible on all focusable elements (Tailwind `focus-visible:` variants).
- Color-contrast ratio ≥ 4.5:1 for all text; Tailwind config defines an accessible palette.

### 9.2 Design system consistency

- All shadcn primitives styled with a single Tailwind theme defined in `tailwind.config.ts`.
- CSS variables for primary/secondary colors, border radii, shadows → easy to swap for custom branding later.
- Inter font as default; fallback to system-ui.
- 8px spacing grid throughout.
- Loading states: skeleton components (shadcn's `Skeleton`) on every data-fetching page.
- Empty states: illustrative + action-prompting, not just "no items".
- Error boundaries on every route tree; errors surface as Sonner toasts for transient issues, full-page error state for unrecoverable.

### 9.3 Playwright end-to-end suite

Port OAB's Playwright specs where they apply; add Composer-specific flows.

- **Auth:** standalone login, SSO login (mocked Azure AD via `next-auth` test harness), role-gated routing, logout, refresh on expired token.
- **End User:** list workflows, open one, submit input, watch progress, see output.
- **Designer:** new workflow, add nodes, connect, save, publish, confirm external URL in dialog.
- **Admin:** promote user, revoke an API key, share an MCP, toggle a built-in tool, rotate an LLM key.
- **External invoke:** create API key, hit `/api/run/{slug}` with curl in a Playwright-driven Node test, confirm 202 + stream URL + eventual completion.

### 9.4 Load testing

Single scenario: 10 concurrent users running small workflows. Confirm:
- No 500s.
- Rate limits kick in at expected thresholds.
- WebSocket reconnections don't leak.

Deferred beyond that; full load plan is a post-cutover concern.

---

## 10. Decisions (for ADR-0023)

Backfill into `docs/design/decisions.md` as ADR-0023 at phase-exit:

1. **Fresh UX, not OAB port.** OAB's code is behavioral reference; Composer's frontend is re-built in Next.js 14 + Tailwind + shadcn/ui for enterprise polish.
2. **Monorepo, not split repo.** `composer/frontend/` alongside `composer/src/`. Shared OpenAPI types flow cleanly; one PR per full-stack change.
3. **Single Next.js app, role-aware routes.** One codebase, three route trees (`/designer/*`, `/runs/*`, `/admin/*`), guarded at the layout level by role checks. No separate deploys per role.
4. **Tailwind + shadcn/ui over Ant / Mantine.** Tailwind's utility model + shadcn's copy-into-repo primitives give full design control; Ant's "enterprise look" is dated and hard to re-theme.
5. **Three audiences, not two.** Phase 10 introduces Admin as a first-class audience with dedicated UI (user management, tool/MCP publishing, LLM keys, workflow override). OAB had only Designer + End User.
6. **Unified Tools palette for designers.** Built-in Composer providers + shared MCPs render as one palette; the MCP vs code-provider distinction lives only in the Admin UI.
7. **Email as Azure SSO identity link.** Phase 9's email-as-cross-system-identity policy extends to Azure SSO. Standalone `/auth/login` preserved. User.passwordHash becomes nullable.
8. **Role-based workflow access, no groups.** `isPublic=true` → any authenticated user + any API key; `isPublic=false` → owner + admin only. Group-based ACL is a future phase if needed.
9. **Production-workflow state + externalSlug + ApiKey.** OAB's "production" concept ports to Composer as `Workflow.isProduction` + globally-unique `externalSlug`. External invoke via `POST /api/run/{slug}` with `Authorization: Bearer ck_<key>`.
10. **Async default, sync opt-in.** External invoke returns 202 + stream URL by default; `?sync=true` (or body `sync: true`) waits up to `timeoutSeconds` (max 300).
11. **No workflow versioning.** Editing a production workflow updates the latest; no snapshots. Designers are expected to test in draft copies. Versioning is a future phase.
12. **SSO exchange endpoint, not frontend-signed JWTs.** NextAuth validates Azure token, then calls `POST /auth/sso-exchange` to mint Composer tokens. Composer stays the JWT authority.

---

## 11. Risks + open questions

| Risk / Q | Mitigation |
|---|---|
| 3-audience single-app bundle size balloons | Route-based code splitting (Next.js default); audit bundle per route in 10f |
| Azure AD tenant config friction | Document in `docs/deployment/azure-sso.md` (add in 10f); require setup step before `sso_enabled=true` |
| Production workflow edits break external callers | 10f docs runbook: edit a draft copy, then `PUT` with isProduction flip; version snapshots remain a future phase |
| Slug collisions at scale | Globally unique + collision-at-publish error; admin can force-rename any workflow's slug via `/admin/workflows` |
| OpenAPI TS client regeneration drift | `npm run generate-client` in CI pipeline; PR fails if generated client is out of sync |
| NextAuth v5 still in beta at spec time | If NextAuth v5 GA slips, fall back to v4 (which has Azure AD + Credentials providers) |
| React Flow API changes between 11.x and 12.x | Pin to a specific major in `package.json`; upgrade is its own PR |
| WebSocket reconnection after token refresh | Client listens for 4401, refreshes token, re-connects with new bearer subprotocol |
| Load on Vercel single-worker for streaming | WebSocket state is in-memory in Phase 9; Vercel Edge functions don't support WS persistence — use a single long-running Node server (Vercel Serverless Function with a long timeout, OR a separate process). Covered in 10f deployment docs |
| Migration from OAB's Playwright tests | Review each OAB test; port if it still describes a Composer-true flow, else rewrite from scratch |

---

## 12. Error model

- Frontend fetch wrappers parse Composer's error shape (`{detail: str}` or Pydantic validation arrays) and surface as typed errors.
- 401 → silent refresh-then-retry; if refresh fails, log out.
- 403 → toast "you do not have permission"; no redirect.
- 404 on authorized routes → show "not found" page with a link back to role home.
- 409 (slug conflict etc.) → inline form error with the conflicting field highlighted.
- 422 (validation) → inline form errors per Pydantic `loc` path.
- 429 → toast "too many requests" + automatic retry-after delay.
- 5xx → Sonner toast; Sentry-style reporting deferred to future phase.

---

## 13. Phase-exit criteria

All six sub-phases shipped. Specifically:
- [ ] `POST /api/run/{slug}` works end-to-end (create workflow → publish → create API key → curl invoke → execution row created → output returned).
- [ ] Azure SSO login issues a working Composer session for a new Azure user (user auto-provisioned).
- [ ] End User UI: user can list, run, and view results of a published workflow.
- [ ] Admin UI: admin can promote a user, share an MCP, toggle a built-in tool, manage LLM keys.
- [ ] Designer UI: designer can build a workflow visually, save, publish, and the external URL works.
- [ ] Playwright end-to-end suite passes on CI.
- [ ] Accessibility: 0 axe-core violations at severity ≥ moderate.
- [ ] CHANGELOG + CLAUDE.md phase table updated; ADR-0023 backfilled.
- [ ] Unit tests green on both Python (baseline Phase 9: 600) and TypeScript (new suite).
- [ ] ~2–4 new integration tests green against real Neon.

---

## 14. Out-of-scope follow-ups (future phases)

- Per-workflow version snapshots + rollback.
- Group-based workflow access.
- Workflow templates marketplace.
- Self-serve password reset.
- Multi-tenant.
- Mobile-first / PWA.
- Custom-branded visual theme (the CSS variables are ready; just swap values).
- Offline-capable workflows.
- Real-time collaborative editing on the Designer canvas.
- Advanced observability (distributed traces, per-user usage dashboards).
