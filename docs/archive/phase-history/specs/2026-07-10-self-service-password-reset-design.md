# Self-Service Password Reset — Design

**Status:** Approved (2026-07-10)
**Context:** Follow-up to `docs/archive/phase-history/specs/2026-07-09-account-workflow-sharing-design.md` (ADR-0024), which deliberately shipped admin-only password reset (no email dependency). The user has since asked for a self-service "Forgot password?" flow. This spec adds it as an additive layer on top of the existing `password_change` JWT mechanism — no rework of the admin-reset flow.

## Problem statement

Composer's login page has no "Forgot password?" option. A user who forgets their password today has no recourse except asking an admin to reset it via Admin → Users → Reset password. The user wants a standard self-service email-link flow added, reusing existing infrastructure wherever possible since this "should not be a big development."

## What already exists (do not re-derive)

- `src/security/jwt.py` already has a `password_change` JWT type (`create_password_change_token`, `verify_password_change_token`), used today only by the admin-forced-reset flow (ADR-0024).
- `src/integrations/email/resend.py` has `ResendEmailProvider` — a plain, workflow-agnostic async HTTP client (`send_email(payload)`) already used by the workflow email node executor. Reusable directly outside workflow execution.
- `src/config.py` has `resend_api_key: str` (env `RESEND_API_KEY`), synced from Postgres via `src/security/key_sync.py` the same way other provider keys are. **Currently broken in this dev environment** — the DB-stored encrypted key fails to decrypt (`authentication tag mismatch`), likely an `ENCRYPTION_KEY` mismatch. This needs a working key before the feature can send real email; not a blocker for building the code, but flagged so it isn't mistaken for a code bug during testing.
- Resend account is on the free tier; the only verified sending domain is **`script-research.online`** (confirmed by the user via the Resend dashboard) — this is unrelated to the app's own domain (`flowcomposer.online`) and that's fine; they're independent (sender domain vs. app domain).
- No existing transactional-email pattern in the codebase (this is the first non-workflow email use case).
- No `FRONTEND_URL`-equivalent backend setting exists yet for building absolute links back to the frontend from a backend-sent email.

## Design

### Backend

**New settings** (`src/config.py`):
- `resend_from_email: str = Field(default="noreply@script-research.online", ...)`
- `frontend_url: str = Field(default="http://localhost:3000", ...)` — production env sets this to `https://www.flowcomposer.online`.
- `rate_limit_forgot_password_per_minute: int = 5` — IP-keyed (caller is anonymous, no user_id yet), same pattern as `auth_register`/`auth_login`.
- Bump `jwt_password_change_ttl_seconds` default from `600` (10 min) to `1800` (30 min) — this TTL is now shared by both the admin-handoff flow (where 10 min was originally fine since it's interactive) and the new email flow (where a user needs time to check their inbox). Raising it uniformly is simpler than adding a second TTL setting, and 30 min is a reasonable value for both.

**New email helper** (`src/integrations/email/resend.py` or a new small sibling module — implementer's call): a `send_password_reset_email(to: str, reset_link: str) -> None` function that builds a minimal HTML body containing the link and calls `ResendEmailProvider(get_settings().resend_api_key).send_email(...)` with `from=get_settings().resend_from_email`. Errors from Resend should be logged but must **not** leak into the `/auth/forgot-password` response (which always returns 204 regardless — see below), since surfacing a delivery failure would both leak account-existence information and give an attacker a signal.

**`POST /auth/forgot-password`** (`src/api/auth_standalone.py`):
- Body: `{email}`.
- Rate-limited via `rate_limit_forgot_password_per_minute`, keyed by IP (same `enforce()`/`RateLimiter` pattern as `register`/`login`).
- Looks up the user by email. If found AND `passwordHash is not None` (i.e., a real password-based account, not SSO-only): generate a `password_change` token via the existing `create_password_change_token`, build `f"{settings.frontend_url}/reset-password?token={token}"`, send the email.
- **Always returns `204 No Content`**, regardless of whether the email matched a real, password-based, existing account — this is the standard no-account-enumeration pattern. The frontend shows the same generic message either way.

**`POST /auth/reset-password`** (`src/api/auth_standalone.py`):
- Body: `{token, newPassword}`.
- Verifies `token` via the existing `verify_password_change_token` directly (not the `get_user_id_allow_password_change` dependency, since that also accepts normal access tokens — this endpoint must accept **only** a `password_change` token, since it's reachable by a fully anonymous, logged-out caller). Invalid/expired token → 400.
- **Does not require or check a current password** — this is the key difference from `/auth/change-password`. The emailed token itself is the proof of identity (standard forgot-password pattern; the user forgot their password, so asking for it defeats the purpose).
- Sets the new password hash, clears `mustChangePassword` (in case the account also had a pending admin-forced reset — this flow clears it the same way `/auth/change-password` does).
- Rate-limited the same way (per-IP, reusing `rate_limit_forgot_password_per_minute` or a matching new setting — implementer's call, keep it simple).

### Frontend

- **Login page** (`frontend/app/(auth)/login/page.tsx`): add a "Forgot password?" link near the password field, pointing at `/forgot-password`.
- **New `frontend/app/(auth)/forgot-password/page.tsx`**: a plain email-input form. On submit, calls a new `composerForgotPassword(email)` (plain fetch, no NextAuth involved, mirroring `composerLogin`'s style in `frontend/lib/composer-api.ts`). Always shows the same generic success message ("If that email is registered, we've sent a reset link") regardless of the actual backend response — never surface a distinguishing error, to preserve the no-enumeration guarantee end-to-end.
- **New `frontend/app/(auth)/reset-password/page.tsx`**: reads `token` from the URL query string (`useSearchParams`), shows a single new-password field (no current-password field — matching the backend), calls a new `composerResetPassword(token, newPassword)`. On success, redirects to `/login` with a success toast. **This page must work for a fully anonymous, logged-out visitor** — it must not be gated by `requireAnySession()` or any session-requiring layout (unlike `/change-password`, which requires the user to already be mid-session).
- `frontend/middleware.ts`: add `forgot-password` and `reset-password` to the matcher's exclusion list alongside `login`/`register`, for consistency (the mustChangePassword-redirect check in the current middleware doesn't apply to anonymous, session-less visitors anyway, but excluding these routes keeps the intent explicit and matches how `login`/`register` are already handled).

### Out of scope

- No "resend the email" cooldown UI beyond the rate limit itself.
- No email verification step for new registrations (unrelated, pre-existing gap, not touched here).
- Fixing the currently-broken Resend key decryption in this dev environment is an operational task, not a code change — flagged as a prerequisite for live testing, not part of this spec's deliverables.

## Testing plan

- Unit tests: `/auth/forgot-password` returns 204 for both existing and non-existing emails (and for SSO-only accounts with `passwordHash=None`), only sends email in the real-account case (mock the email helper); `/auth/reset-password` accepts a valid `password_change` token and rejects an access/refresh token, an expired token, and a malformed token; rate-limit enforcement on both routes.
- Frontend: no dedicated component tests planned (consistent with this codebase's existing pattern of not unit-testing simple form pages like `/login`, `/register`) — manual/live verification once the Resend key is working.

## ADR

A new ADR entry will be added to `docs/decisions.md` at the end of implementation, following the same lockstep-docs convention as ADR-0024/0025/0026.
