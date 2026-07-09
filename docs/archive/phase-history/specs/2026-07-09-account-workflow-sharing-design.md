# Account + Workflow Sharing — Design

**Status:** Approved (2026-07-09)
**Related ADRs to add during implementation:** ADR-0024 (password reset policy), ADR-0025 (workflow assignment model), ADR-0026 (autosave persistence policy)

## Problem statement

Three issues were reported against the running application:

1. No user can recover a forgotten password, and no admin can reset one for them.
2. A workflow can only ever have a single "owner" (`Workflow.userId`) — there is no way to give more than one user access to the same flow.
3. Reported as "flow details get lost during transfer, leaving only Start+End nodes." Investigation (below) found the reassign-owner endpoint never touches `nodes`/`edges` — it only updates `userId`. The actual root cause is that the Designer has **no autosave**: nodes/edges only reach Postgres when a user explicitly clicks Save. If an owner edits a flow and never saves, the DB row genuinely only ever held the Start+End scaffold from creation, and this becomes visible the moment a different person (the new assignee/owner) opens it fresh. This is reframed as a data-loss-prevention gap, not a transfer bug.

## Investigation findings (do not re-derive)

- `User` model (`prisma/schema.prisma`) has no reset-token or password-change fields. Auth routes (`src/api/auth_standalone.py`) are only register/login/refresh/disconnect. Admin user routes (`src/api/admin_users.py`) have no password-reset action. Confirmed gap.
- `Workflow.userId` is a single scalar FK (`prisma/schema.prisma`); `PATCH /workflows/{id}/owner` (`src/api/workflows.py:427-454`) does `update(data={"userId": target_user_id})` — strictly 1:1, and reassigning necessarily un-assigns the previous user.
- OAB's own schema (`open-agent-builder/convex/schema.ts`, read-only reference) also only ever had a single `assignedTo` string — OAB never supported many-to-many assignment either. This work is a genuine enhancement over OAB, not a restoration of prior behavior.
- The Designer (`frontend/app/designer/[workflowId]/page.tsx`) persists nodes/edges only via an explicit Save button (`save-controls.tsx`) → `PUT /workflows/{id}`. No debounce, no localStorage, no draft table exists anywhere in the schema or frontend.

## A. Password reset

**Self-service change (new):** `POST /auth/change-password` (authenticated) — body `{current_password, new_password}`. Verifies `current_password` against `passwordHash` via existing `verify_password` (`src/security/passwords.py`), then re-hashes and stores `new_password`. Clears `mustChangePassword` if set.

**Admin-forced reset (new):** `POST /admin/users/{id}/reset-password` (admin-role only). Generates a random temporary password (`secrets.token_urlsafe`), hashes and stores it, sets `User.mustChangePassword = true`, and returns the plaintext temp password **once** in the response body (never persisted in plaintext). No dedicated audit-log table exists in Composer today (`admin_users.py` relies on standard app logging plus DB-level history via preserved foreign keys) — this action follows that same existing convention rather than introducing new audit infrastructure.

**Schema change:** add `mustChangePassword Boolean @default(false)` to `User`.

**Login enforcement:** `POST /auth/login` (`auth_standalone.py`), on success, checks `mustChangePassword`. If true, issue the same JWT format as a normal session but with an added claim `scope: "password_change_only"`, and return a response flag `{"must_change_password": true}` alongside it instead of a normal session flag. Route handlers other than `/auth/change-password` reject tokens carrying that scope claim (403). The frontend routes the user to a forced change-password screen using that restricted token. After a successful `POST /auth/change-password`, `mustChangePassword` clears and the user must log in again to get a normal-scoped session.

## B. Many-to-many workflow assignment

**Schema change:** new `WorkflowAssignment` model — `id`, `workflowId` (FK → `Workflow`), `userId` (FK → `User`), `assignedAt`, `assignedById` (FK → `User`, who granted it). Unique constraint on `(workflowId, userId)`.

`Workflow.userId` remains the single **owner** (creator) — unchanged in meaning. Assignment is additive sharing, layered on top, not a replacement.

**New endpoints** (`src/api/workflows.py`):
- `POST /workflows/{id}/assignments/{user_id}` — grant access. Allowed for the workflow's owner or an admin.
- `DELETE /workflows/{id}/assignments/{user_id}` — revoke access. Same permission rule.
- `GET /workflows/{id}/assignments` — list current assignees.

**Authorization change:** the existing owner-only 404 check in `GET /workflows/{id}` (and other workflow-scoped routes covered by ADR-0021) relaxes from "owner or admin" to "owner, assignee, or admin" (in addition to the existing `isPublic` bypass).

The existing `PATCH /workflows/{id}/owner` stays as a distinct "transfer ownership" action (single owner, unaffected by the assignment list) — it is not renamed or merged with assignment.

### B.1 Frontend UI changes

Investigation of the current frontend found no existing multi-user picker anywhere in the codebase — the closest precedents are the reassign-owner dialog's single-select search box (`frontend/components/composer/reassign-owner-dialog.tsx:83-125`) and the MCP server sharing toggle (`frontend/components/composer/mcp-shared-toggle.tsx`), which is an all-or-nothing boolean, not a per-user list. Neither is reusable as-is, so this needs net-new UI:

- **New `ManageAssigneesDialog` component** (frontend/components/composer/), modeled on the reassign-owner dialog's search/filter pattern but rendering the matched users as a checkbox list instead of single-click-to-select, plus a list of currently-assigned users with a remove (×) action per row. Backed by three new API client functions in `frontend/lib/api/workflows.ts` following the existing verb+`Workflow` naming convention: `listWorkflowAssignments(workflowId)`, `assignWorkflowUser(workflowId, userId)`, `unassignWorkflowUser(workflowId, userId)` — hitting the `/workflows/{id}/assignments` endpoints from B above.
- **Entry points:** a "Manage access" button/action alongside the existing "Reassign" button in the admin all-workflows table (`frontend/app/admin/workflows/page.tsx:248-251`), available to admins for any workflow; and a matching action surfaced to workflow owners themselves from their own "My workflows" list (`frontend/app/designer/page.tsx`), since B's authorization rule allows owners (not just admins) to manage their own workflow's assignees.
- **"My workflows" list must include assigned-not-owned flows:** `listWorkflows({ mine: true })` (`frontend/lib/api/workflows.ts:8-22`) currently filters strictly by `Workflow.userId` server-side (`frontend/app/designer/page.tsx:15-21`). This query needs to change to match owner **or** assignee, otherwise a user granted access via assignment still won't see the flow anywhere. Recommend a visual distinction in the list (e.g. an "Owner" vs "Shared with you" badge) so users can tell the two apart.

## C. Autosave + unsaved-changes safeguard

**Autosave:** the Designer debounces node/edge changes (~3s after the last edit) and fires the existing `PUT /workflows/{id}` automatically, reusing the current save path — no new backend endpoint needed.

**Save-status indicator:** a small state machine (`idle` → `saving` → `saved` / `error`) surfaces as a status label near the Save button (e.g. "All changes saved" / "Saving…" / "Unsaved changes" / "Save failed"). Manual Save remains available and is not removed.

**Navigation guard:** a `beforeunload` handler blocks/warns on tab-close or navigation while status is anything other than `saved`.

**Explicit scope boundary:** no concurrent multi-editor conflict resolution is introduced — last-write-wins remains the behavior, consistent with the current single-editor assumption. If concurrent editing becomes a real scenario later, that's a separate design.

## Migration notes

- `mustChangePassword` backfills to `false` for all existing users (default value covers this — no data migration script needed).
- `WorkflowAssignment` is a new, empty table — no backfill required. Existing single-owner access is unaffected since owner checks remain valid alongside the new assignee check.
- Both schema changes go through `prisma migrate dev`.

## Testing plan

- Unit tests: password hashing/reset flow (including the `mustChangePassword` gate on login), assignment CRUD + authorization matrix (owner/assignee/admin/stranger × public/private), autosave debounce logic and save-status transitions (frontend).
- Integration tests (`@pytest.mark.integration`): full reset-password → forced-change → login cycle against a real Neon dev DB; assignment grant/revoke against real DB with authz assertions; PUT-based autosave round-trip.
- Playwright e2e (existing suite from Phase 10): admin grants a second user access via `ManageAssigneesDialog`, that user sees the flow appear in their "My workflows" list, opens and edits it; owner revokes access and the flow disappears from the assignee's list on next load.

## Out of scope

- Email-based self-service password reset (explicitly deferred per the "admin-only, no email" decision).
- Per-assignee permission levels (e.g. view vs edit) — assignment is all-or-nothing access for now.
- Real-time collaborative editing / conflict resolution for the Designer.
