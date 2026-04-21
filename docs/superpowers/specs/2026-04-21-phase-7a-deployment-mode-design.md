# Phase 7a — Deployment-mode toggle + auth middleware

**Target exit date:** 2026-04-22
**Author:** Composer team
**Depends on:** Phase 1 (JWT primitives as library code per ADR-0005), all subsequent phases (executors currently hardcode `user_id="dev"`).
**Supersedes:** ADR-0005's "auth middleware is Phase 7" — this *is* that work, split out as 7a.
**Re-sequences:** Phase 5 (user-approval + SSE) now runs *after* Phase 7a, since user-approval needs authenticated user context.

---

## 1. Goal

Ship a single environment variable, `COMPOSER_DEPLOYMENT_MODE ∈ {"standalone", "embedded"}`, that fully determines four interlocking behaviors:

| Behavior | Standalone | Embedded |
|---|---|---|
| JWT validation | HS256 with `JWT_SECRET` (Composer owns the secret) | RS256 against `IEP_JWKS_URL` OR HS256 with `IEP_SHARED_SECRET` (IEP owns) |
| `User` table | Populated via `/auth/register` | Empty; `userId` strings flow in as opaque JWT `sub` values |
| `/auth/*` endpoints | Exposed (`register`, `login`, `refresh`, `me`, `disconnect`) | Only `/auth/me` exposed |
| CORS origin | Permissive in dev, explicit allowlist in prod | Locked to `IEP_UI_ORIGIN` |

Every Composer route handler replaces `user_id="dev"` hardcoding with `Depends(get_current_user_id)`. A dev-mode escape hatch (ADR-0015) keeps existing integration tests working.

## 2. Non-goals for this phase

- Role-based access control. Role enum is in the `User` schema for future use; no enforcement.
- SSO / OAuth login (Google, GitHub, SAML, etc.). Password-only in standalone 7a.
- Email verification, password reset, password complexity rules. Phase 7b.
- Refresh-token revocation lists. In-memory + per-JWT expiry only for 7a. Phase 7b adds a revocation table.
- Rate limiting on `/auth/login`. Phase 7b.
- Live toggling between modes without a redeploy. Mode is read at startup (see ADR-0014).
- Onboarding UI. Phase 10.
- Multi-tenant workspaces / teams. Phase 9+.
- Password hashing algorithm choices beyond bcrypt. bcrypt with configurable rounds is sufficient.

## 3. Success criteria

Phase 7a exits when:

1. All unit tests pass: `ruff`, `pyright --strict`, `pytest -m "not integration"` green on `main`.
2. Four integration suites pass against real Neon:
    - **Standalone register → login → authenticated call → refresh → /me** — happy path through the auth lifecycle.
    - **Embedded with real IEP JWT** — uses a seeded JWT (signed with a known shared secret in `.env`), makes an authenticated call, succeeds; unsigned/wrongly-signed token → 401.
    - **Dev-mode fallback** — Phase 3a/3b/4a/4b integration tests (which don't set `Authorization`) continue to pass via ADR-0015 fallback.
    - **Mode-based 404** — in embedded mode, `POST /auth/register` returns 404 (route not registered).
3. No source file contains `user_id="dev"` as a literal outside of `ADR-0015`-documented dev-mode fallback inside `src/security/auth.py`.
4. CHANGELOG + CLAUDE.md reflect 7a ✅; ADR-0014 + ADR-0015 committed with `Implemented by` backfilled.

## 4. Architecture in two diagrams

### 4.1 Request flow

```
  Incoming HTTP request
         │
         ▼
  ┌──────────────────────┐
  │  FastAPI middleware  │
  └──────────┬───────────┘
             │ extracts Authorization header
             ▼
  ┌──────────────────────────┐
  │ get_current_user_id dep  │
  │   - settings.mode=="..." │
  └──────────┬───────────────┘
             │
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
 standalone         embedded
  HS256 verify       RS256 / JWKS verify (or HS256 shared)
  sub → user_id      sub → user_id
    │                 │
    └────────┬────────┘
             │
             ▼ (if no header AND ENV=development)
  ┌──────────────────────┐
  │ Dev-mode fallback    │
  │ return "dev"         │  ← ADR-0015
  └──────────┬───────────┘
             │
             ▼
  Route handler receives user_id as a str
```

### 4.2 Startup router registration

```
create_app():
    if settings.deployment_mode == "standalone":
        app.include_router(auth_standalone_router)  # /auth/register, login, refresh, disconnect
    # /auth/me registered in both modes:
    app.include_router(auth_common_router)
    # ...other routers...
```

## 5. Prisma schema additions — `User` table

```prisma
// Phase 7a — populated ONLY in standalone deployment mode.
// Embedded deployments do not write to this table (users are externally
// owned by IEP); userId strings stored on McpServer / Workflow / etc.
// are opaque JWT sub claims in that case.

enum UserRole {
  admin
  member
}

model User {
  id            String    @id @default(cuid())
  email         String    @unique
  passwordHash  String    @map("password_hash")
  displayName   String?   @map("display_name")
  role          UserRole  @default(member)
  createdAt     DateTime  @default(now()) @map("created_at")
  updatedAt     DateTime  @updatedAt       @map("updated_at")

  @@map("users")
  @@index([email])
}
```

- `passwordHash` holds bcrypt output (algorithm + rounds + salt + digest concatenated per bcrypt's standard format — one column).
- `role` is an enum so future RBAC is an additive change, not a schema migration.
- `email` has a unique index → `register` surfaces a clean 409 Conflict for duplicates.
- `@default(cuid())` means Composer mints user IDs; not an auto-increment int.

Embedded mode never INSERTs rows here but may SELECT if someone asks `/auth/me` with a `sub` that matches a local row (we'd return the row's display info). For purity in 7a, `/auth/me` in embedded mode returns JWT claims without touching the `User` table.

## 6. Settings additions

Append to `src/config.py`:

```python
# ─── Deployment mode (Phase 7a, ADR-0014) ────────
deployment_mode: Literal["standalone", "embedded"] = Field(
    default="standalone",
    description="'standalone' owns users + issues its own JWTs; 'embedded' trusts JWTs from IEP.",
)

# ─── Embedded-mode (IEP integration) ─────────────
iep_jwt_issuer: str = Field(default="", description="Expected `iss` claim in IEP-issued JWTs.")
iep_jwks_url: str = Field(default="", description="RS256: URL to fetch IEP's public keys.")
iep_shared_secret: str = Field(default="", description="HS256: shared secret with IEP (fallback path).")
iep_ui_origin: str = Field(default="", description="Exact origin (scheme+host+port) allowed by CORS when embedded.")

# ─── Standalone password hashing ─────────────────
bcrypt_rounds: int = Field(default=12, description="bcrypt cost factor for password hashes.")
```

`deployment_mode` is read ONCE at `create_app()` time. Changing the env var requires a redeploy — documented in ADR-0014.

## 7. `src/security/auth.py` — middleware primitives

New module. Existing `src/security/jwt.py` (Phase 1) provides low-level encode/decode helpers; `auth.py` composes them into the request-scoped middleware.

```python
# src/security/auth.py (new)

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from src.config import Settings, get_settings
from src.security.jwt import decode_hs256_jwt


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[len("bearer "):].strip() or None


async def _verify_standalone_jwt(token: str, settings: Settings) -> str:
    """Decode HS256 JWT signed with Composer's own JWT_SECRET; return sub."""
    try:
        claims = decode_hs256_jwt(token, settings.jwt_secret)
    except Exception as exc:
        raise AuthError(f"invalid JWT: {exc}") from exc
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")
    return str(sub)


async def _verify_embedded_jwt(token: str, settings: Settings) -> str:
    """Verify IEP-signed JWT.  Prefers JWKS (RS256) if iep_jwks_url is set;
    falls back to HS256 with iep_shared_secret if not."""
    if settings.iep_jwks_url:
        # Phase 7a v1: not implemented (needs JWKS caching + RS256 decode).
        # Fall through to shared secret path.
        pass
    if not settings.iep_shared_secret:
        raise AuthError(
            "embedded mode: no IEP JWT verification configured "
            "(set IEP_JWKS_URL or IEP_SHARED_SECRET)"
        )
    try:
        claims = decode_hs256_jwt(token, settings.iep_shared_secret)
    except Exception as exc:
        raise AuthError(f"invalid IEP JWT: {exc}") from exc
    if settings.iep_jwt_issuer and claims.get("iss") != settings.iep_jwt_issuer:
        raise AuthError("IEP JWT issuer mismatch")
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")
    return str(sub)


async def get_current_user_id(
    request: Request,
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> str:
    """FastAPI dependency — returns the authenticated user_id, or 'dev' in
    the documented development-mode fallback (ADR-0015).
    """
    token = _extract_bearer(request)
    if token is None:
        # Dev-mode fallback (ADR-0015)
        if settings.environment == "development":
            return "dev"
        raise AuthError("missing Authorization header")

    if settings.deployment_mode == "embedded":
        return await _verify_embedded_jwt(token, settings)
    return await _verify_standalone_jwt(token, settings)


__all__ = ["AuthError", "get_current_user_id"]
```

**JWKS (RS256) is a Phase 7b stretch goal.** 7a ships with HS256-shared-secret validation for IEP; the code structure above leaves an obvious seam for JWKS to drop in later without changing the public dependency signature.

## 8. `/auth/*` endpoints

### 8.1 Standalone router — `src/api/auth_standalone.py` (new)

Registered only when `settings.deployment_mode == "standalone"`.

```
POST /auth/register          → 201 { id, email, displayName, accessToken, refreshToken }
POST /auth/login             → 200 { accessToken, refreshToken }
POST /auth/refresh           → 200 { accessToken, refreshToken }  (rotates refresh)
POST /auth/disconnect        → 204                                (client-side token drop for now)
```

Request / response shapes via Pydantic. Password handling uses `bcrypt` library (already in `pyproject.toml` from Phase 0, or we add it here).

Tokens minted via `src/security/jwt.py` helpers — access tokens expire per `settings.jwt_access_ttl_seconds` (1h default), refresh per `settings.jwt_refresh_ttl_seconds` (7d default).

### 8.2 Common router — `src/api/auth_common.py` (new)

Registered in both modes.

```
GET /auth/me
   standalone: looks up User by id=request.state.user_id; returns {id, email, displayName, role}
   embedded:   returns {id: sub, claims: {iss, exp, iat, ...}}  — direct JWT claims reflection
```

Uses `Depends(get_current_user_id)` so 401 is handled uniformly.

### 8.3 Bcrypt library

`bcrypt` (Python wrapper for libbcrypt) is the industry-standard choice. If it's not already in `pyproject.toml`, this task adds it. Not a major dep — small wheel, widely supported.

## 9. Migrating existing routes off `user_id="dev"`

Grep reveals ~4 call sites hardcoding `"dev"`:

| Location | Current | After |
|---|---|---|
| `src/api/mcp_servers.py` (create, list, test-connection, disconnect, oauth/authorize) | `"dev"` literal | `user_id: str = Depends(get_current_user_id)` + pass through to Prisma writes / permission checks |
| `src/executors/agent.py arun()` | `user_id="dev"` in BuildContext | Phase-7a: reads `state.get("user_id")` set by `LangGraphExecutor.run` from the calling API context — see §9.2 |
| `src/executors/mcp.py arun()` | same | same |
| `src/executors/extract.py arun()` | no user_id needed | unchanged |

### 9.1 API routes

Each route handler that currently hardcodes `"dev"` declares `user_id: str = Depends(get_current_user_id)` and uses it. Routes that don't write user-scoped data (like `/workflows` when listing public templates — there aren't any such routes in 7a) can omit the dependency.

### 9.2 Executors — thread user_id through state

Executors receive a `state: WorkflowStateDict`. Phase 7a adds an optional `user_id: str | None` key on `WorkflowStateDict`. `LangGraphExecutor.run(execution_id)` currently reads the `WorkflowExecution` row which has a `userId` column — we propagate that into the initial state before calling `compiled.ainvoke`. Executors then read `state.get("user_id")`.

The Agent + MCP executors both need this — MCP OAuth token lookup is per-user (Phase 3b), agent execution tags LangSmith traces with user context (future).

No schema change needed — `WorkflowExecution.userId` already exists (Phase 1).

### 9.3 Dev-mode backward compatibility

Our ~8 LLM-backed integration tests don't set Authorization headers. Via ADR-0015, `get_current_user_id` returns `"dev"` when `ENVIRONMENT=development` + no header. Tests keep passing without modification. The fallback is forbidden in `production`.

## 10. CORS + origin

`src/main.py` already has a `CORSMiddleware` with `allow_origins=["*"]` in dev. Phase 7a adds:

```python
if settings.deployment_mode == "embedded":
    allow_origins = [settings.iep_ui_origin] if settings.iep_ui_origin else []
    allow_credentials = True
else:
    # standalone
    allow_origins = ["*"] if settings.environment == "development" else []
```

Production standalone deployments get an explicit allowlist via a future `composer_ui_origins` setting — not 7a scope (hardcoded empty list in production until then, matching current behavior).

## 11. Error model

- `AuthError` → 401 with a single `detail` string ("invalid JWT", "missing Authorization header", "embedded mode: no IEP JWT verification configured", etc.)
- `UserAlreadyExistsError` → 409 on `/auth/register` with duplicate email
- `InvalidCredentialsError` → 401 on `/auth/login` wrong password
- All other errors → 500 with a generic detail (no password/token leakage)

Password-comparison uses bcrypt's constant-time comparator — no timing-sidechannel on login.

## 12. Test plan

### 12.1 Unit (~22 tests)

- `test_auth.py`:
  - `_extract_bearer` happy / missing / wrong scheme
  - `_verify_standalone_jwt` happy / bad signature / missing sub
  - `_verify_embedded_jwt` happy / wrong issuer / missing config / missing sub
  - `get_current_user_id` dev-mode fallback activates + does not activate in production
  - `get_current_user_id` routes to standalone vs embedded verifier per settings
- `test_auth_standalone.py` (routes with Prisma mocked):
  - register: happy, duplicate email → 409
  - login: happy, wrong password → 401, unknown email → 401
  - refresh: happy, expired refresh → 401
  - disconnect: 204
- `test_auth_common.py`:
  - `/auth/me` standalone returns User row fields
  - `/auth/me` embedded returns JWT claims
  - both: missing auth → 401 in production, → "dev" in development

### 12.2 Integration (against real Neon)

- `test_standalone_auth_lifecycle.py` — register → login → authenticated call → refresh → me
- `test_embedded_auth.py` — mints a JWT with `IEP_SHARED_SECRET`, makes an authenticated call, succeeds
- `test_dev_mode_fallback.py` — no Authorization header in `ENVIRONMENT=development` → call succeeds as user="dev"
- `test_mode_based_route_registration.py` — in embedded mode, `POST /auth/register` → 404

### 12.3 Backward compat

Run the entire existing integration suite (Phase 3a + 3b + 4a + 4b): 15+ tests, all currently pass. Must continue to pass after 7a's migration.

## 13. Phase-exit checklist

- [ ] All unit tests green: `ruff`, `pyright --strict`, `pytest -m "not integration"`.
- [ ] Four new integration suites green against real Neon.
- [ ] All existing integration tests (Phases 2-4b) continue to pass via dev-mode fallback.
- [ ] No `user_id="dev"` literal remains in src/ except the single documented fallback in `src/security/auth.py`.
- [ ] Prisma `User` table migrated.
- [ ] CHANGELOG Phase 7a section.
- [ ] CLAUDE.md phase table: 7a ✅ Complete. Phase 5 (user-approval + SSE) ⏭ Next.
- [ ] ADR-0014 + ADR-0015 `Implemented by` backfilled.

## 14. Risks and mitigations

**Risk 1.** Mode-mismatch between what env vars are set and `deployment_mode`. Example: `deployment_mode=embedded` but no `IEP_SHARED_SECRET`. **Mitigation:** startup check in `create_app()` — if embedded, require exactly one of `IEP_JWKS_URL` or `IEP_SHARED_SECRET` + require `IEP_JWT_ISSUER`. Refuse to boot otherwise.

**Risk 2.** Dev-mode fallback ships to production. **Mitigation:** the fallback branch checks `settings.environment == "development"` (read from env var; production deploys set `ENVIRONMENT=production`). There is no env-var-free way to trigger the fallback. ADR-0015 documents this.

**Risk 3.** Bcrypt cost (`bcrypt_rounds=12`) makes registration slow — ~300ms per hash. **Mitigation:** bcrypt hashes are computed inside the event loop via `asyncio.to_thread` so the 300ms doesn't block other requests. Phase 7b can tune rounds.

**Risk 4.** Embedded-mode JWT verification fails silently if IEP rotates secrets. **Mitigation:** JWKS (RS256) path handles rotation natively via published public keys. 7a's shared-secret path requires coordinated redeploy on secret rotation — documented as a limitation. 7b adds JWKS.

**Risk 5.** Existing workflows' `workflow.userId = "dev"` remains forever. **Mitigation:** out of scope for 7a. After 7a ships, new workflows get real user IDs; old rows stay `"dev"` unless explicitly migrated. A one-time data migration can happen when a real user adopts ownership (Phase 10 UI).

## 15. New ADRs

### ADR-0014: Deployment-mode toggle — env var, read at startup

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Composer must run in two deployment shapes: as a standalone product (owns user identity, issues its own JWTs, exposes auth endpoints) and as a module inside the IE platform (IEP owns identity, passes JWTs to Composer). Three mechanisms considered for the switch:
1. **Env var read at startup.** `COMPOSER_DEPLOYMENT_MODE=standalone|embedded`. Immutable within a running process.
2. **DB-backed `SystemConfig` row** read per request (or cached). Live-switchable.
3. **Two separate binaries / packages** — standalone and embedded builds.

**Decision.** Option 1. One env var, `COMPOSER_DEPLOYMENT_MODE`, read once at `create_app()` time. Default: `"standalone"`.

**Alternatives considered.**
- DB flag: rejected. Auth semantics differ fundamentally between modes (which signing algorithm to trust, whether a `User` table is write-scoped, which routes are registered). Flipping live would leave unauthenticated requests hitting half-migrated state. Redeploy is the correct boundary.
- Two binaries: rejected. Doubles CI matrix + artifact count with no real isolation gain — most code is shared.

**Consequences.**
- Changing mode requires redeploy. Operators who switch a live deployment must expect a brief downtime window. Documented.
- Same codebase serves both modes; conditionals live only at FastAPI router registration + inside `get_current_user_id`. No ubiquitous mode-check pollution.
- Env-var-driven config is aligned with IE's Helm / Docker-Compose deployment patterns.

**Implemented by.** Phase 7a (commits TBD).

**Related.** ADR-0004 (Neon-only local dev), ADR-0005 (Phase 1 API surface + JWT library code), ADR-0015 (dev-mode auth fallback).

### ADR-0015: Dev-mode auth fallback — `user_id="dev"` when no Authorization header in `ENVIRONMENT=development`

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 7a migrates all route handlers from `user_id="dev"` hardcoding to `Depends(get_current_user_id)` that requires a JWT. But 15+ existing integration tests across Phases 3a/3b/4a/4b don't set Authorization headers — they assume Composer accepts anonymous calls. Two options:
1. **Update every integration test** to obtain and send a JWT. Substantial refactor; couples integration tests to auth lifecycle.
2. **Keep a fallback:** when `environment=development` AND no header, return `user_id="dev"`. Tests keep working without change.

**Decision.** Option 2. `get_current_user_id` returns `"dev"` if and only if:
- `request.headers.get("authorization")` is missing or empty, AND
- `settings.environment == "development"`.

Production deployments set `ENVIRONMENT=production`; the fallback is unreachable. There is no env-var-free way to trigger it.

**Alternatives considered.**
- Config flag `enable_dev_auth_fallback`: rejected, doubled state surface.
- Test-only fixture that injects auth: considered for Phase 7b, but 7a's minimal churn is preferred.

**Consequences.**
- Dev-mode is explicit: `ENVIRONMENT=production` means real auth required.
- Any production-deploy without `ENVIRONMENT=production` set is a misconfiguration that this fallback would activate. Mitigation: startup logs `"auth: dev-mode fallback ENABLED"` as a prominent warning when `environment=development`. Operators running production-like infra see the log and fix the env var.
- Existing integration tests require zero changes.
- New integration tests that explicitly want to test auth (Phase 7a's test suite) set `Authorization` headers explicitly; they bypass the fallback.

**Implemented by.** Phase 7a (commits TBD).

**Related.** ADR-0014, ADR-0005.

## 16. Execution handoff

After this spec is approved and committed, the plan lands at `docs/superpowers/plans/2026-04-21-phase-7a-deployment-mode-plan.md` and executes via `superpowers:subagent-driven-development`, same cadence as Phases 1–4b. ~11 tasks; one commit per task.
