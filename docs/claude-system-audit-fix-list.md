# Composer System Audit — Claude Fix List

> **Point-in-time record, not current-state documentation.** This 2026-11-02 audit intentionally preserves the evidence and scanner results observed at that time. The CI, dependency, container, and frontend findings have since changed; consult current workflows, lockfiles, and `CHANGELOG.md` before acting on an item.

**Audit date:** 2026-11-02  
**Scope:** backend, frontend, CI/CD, container/runtime configuration, dependency exposure, and automated test coverage.

## How Claude should use this list

1. Re-verify the selected finding against the current branch before editing.
2. Work on one numbered item at a time; do not combine dependency upgrades with unrelated behavior changes.
3. Add a regression test for every security or runtime behavior change.
4. Run the item's targeted checks, then the full backend and frontend gates.
5. Update `CHANGELOG.md` for production behavior, configuration, or dependency changes.
6. Do not run `npm audit fix --force` blindly. Review every major-version migration and resulting lockfile diff.

## P0 — Fix before the next production deployment

### 1. Make internal endpoint authentication fail closed in production

**Evidence**

- `src/api/internal.py:144-146` returns without authenticating when `CLOUD_TASKS_SERVICE_ACCOUNT` is empty.
- `.github/workflows/deploy-gcp.yml:12-18` documents the value as optional and explicitly says the internal routes skip OIDC verification when it is absent.
- `.github/workflows/deploy-gcp.yml:158-170` deploys an empty value as a "safe default."
- `src/config_validation.py:32-66` does not reject this configuration in production.

**Risk:** unauthenticated callers can reach `/internal/*` operations in a production deployment whose service-account variable is missing or accidentally cleared. These routes claim/run executions and invoke maintenance operations.

**Required fix**

- Permit the authentication bypass only in an explicit local-development mode.
- Reject production startup when the Cloud Tasks/Scheduler identity is absent while internal routes are enabled.
- Make the deploy workflow fail before deployment when `CLOUD_TASKS_SERVICE_ACCOUNT` is unset.
- Add tests proving every `/internal/*` route returns 401 in production when identity configuration is missing, invalid, or mismatched.

**Done when**

- No production configuration can enter `_verify_internal_oidc()` and return without authenticating.
- Deployment cannot overwrite a valid service-account value with an empty string.
- Unit tests cover missing header, malformed token, wrong audience, wrong email, and missing production configuration.

### 2. Remediate vulnerable production dependencies

**Evidence**

- `npm audit` reports **51 vulnerabilities**: 4 critical, 18 high, 27 moderate, and 2 low.
- Production-relevant critical/high packages include `next@14.2.35`, `next-auth@5.0.0-beta.31` / `@auth/core`, `postcss`, and transitive HTTP/parser packages.
- `pip-audit` against `.venv` reports **164 advisory matches across 20 packages**. Important runtime packages with fixes include `cryptography`, `starlette`, `python-multipart`, `pydantic-settings`, `pypdf`, `Pillow`, `urllib3`, `langchain*`, `langgraph*`, and `langsmith`.
- `python-jose` and its `ecdsa` dependency have advisories with no fix listed by the scanner, so upgrading the existing package alone may not close that exposure.

**Required fix**

- Triage direct versus transitive and production versus development-only exposure.
- Upgrade the frontend in controlled steps, prioritizing Next.js and Auth.js. Preserve NextAuth behavior with authentication regression tests.
- Upgrade Python dependencies to patched compatible versions and regenerate `uv.lock`.
- Evaluate replacing `python-jose` with a maintained JWT implementation if its open advisory cannot be mitigated.
- Add dependency-audit jobs to CI with a documented allowlist containing owner, rationale, expiry date, and affected surface for any temporary exception.

**Done when**

- Fresh `npm audit` and `pip-audit` outputs contain no unreviewed critical/high production vulnerability.
- Backend tests, frontend unit tests, production build, authentication flows, uploads, PDF parsing, and workflow execution pass after upgrades.
- Any remaining advisory is explicitly documented as non-reachable or accepted temporarily, with an expiry date.

## P1 — Restore regression protection and harden deployment

### 3. Add the frontend to required CI

**Evidence**

- `.github/workflows/ci.yml:9-80` runs only Python/Prisma checks.
- Local frontend type-check, 173 unit tests, and production build pass, but `npm run format:check` fails on **102 files**.
- Playwright tests exist under `frontend/e2e/` but are not run by CI.

**Required fix**

- Add a frontend CI job using the lockfile (`npm ci`).
- Enforce Prettier, TypeScript, Vitest, and `next build`.
- Add Playwright coverage for a production-like authenticated smoke path; split slower end-to-end tests if necessary.
- Format the 102 files in a dedicated mechanical commit so behavioral diffs remain reviewable.

**Done when**

- Pull requests cannot merge when frontend formatting, typing, unit tests, or build fail.
- The formatting-only diff contains no intentional behavior changes.

### 4. Add dependency and secret scanning to CI

**Evidence**

- Current CI contains no `npm audit`, Python advisory scan, secret scan, SAST, or container/image scan.
- Dependency exposure accumulated while all existing CI checks remained green.

**Required fix**

- Add Python and npm vulnerability scanning using lockfile-resolved dependencies.
- Add a secret scanner over commits/PR diffs and a container scan for both images.
- Pin scanner versions and document how false positives are reviewed and expired.
- Ensure scan output is retained as a CI artifact or security report.

**Done when**

- A deliberately vulnerable test fixture or safe scanner self-test proves each gate actually fails.
- Exceptions cannot be permanent, anonymous, or rationale-free.

### 5. Pin CI actions and container build inputs immutably

**Evidence**

- GitHub Actions use mutable major tags such as `actions/checkout@v4` and `docker/build-push-action@v6`.
- `Dockerfile` copies `ghcr.io/astral-sh/uv:latest`, making builds non-reproducible and allowing upstream tag movement to change the binary entering the image.
- Base images use floating `python:3.12-slim` tags rather than digests.

**Required fix**

- Pin third-party actions and container images by reviewed commit/image digest.
- Use Renovate/Dependabot or an equivalent controlled process to propose digest updates.
- Generate and retain an SBOM and provenance for released images.

**Done when**

- Rebuilding the same revision uses the same action and image inputs unless an explicit dependency update is merged.

### 6. Run containers as non-root and minimize proxy trust

**Evidence**

- Backend and frontend Dockerfiles do not define a non-root runtime `USER`.
- Backend `Dockerfile:86-89` trusts forwarded headers from every source with `--forwarded-allow-ips '*'`.

**Required fix**

- Create an unprivileged runtime user, grant only required application/temp-directory access, and set `USER` in both images.
- Determine the actual trusted Cloud Run proxy boundary and restrict forwarded-header trust accordingly. If platform networking makes a narrow IP list impractical, document the compensating boundary and ensure security controls never trust client-supplied forwarded IPs.
- Add container smoke tests proving startup and required file operations work without root.

**Done when**

- Both runtime containers report a non-zero UID.
- Spoofed forwarding headers cannot influence authorization, audit identity, rate-limit identity, or security-sensitive URL generation.

### 7. Add browser security headers

**Evidence**

- `frontend/next.config.mjs` configures only standalone output.
- No repository-level configuration was found for CSP, HSTS, frame restrictions, MIME sniffing protection, referrer policy, or permissions policy.

**Required fix**

- Add a tested production header policy, starting in report-only mode if the current application needs CSP discovery.
- Include at least CSP/frame protection, HSTS on HTTPS production hosts, `X-Content-Type-Options`, `Referrer-Policy`, and an appropriate `Permissions-Policy`.
- Verify OAuth/NextAuth, Google Picker, WebSocket, fonts, and API calls still work under the policy.

**Done when**

- Automated tests assert security headers on representative authenticated and unauthenticated routes.

## P2 — Coverage and maintainability follow-up

### 8. Raise coverage on high-risk, low-coverage modules

**Evidence from the passing backend suite**

- Overall coverage is 81%, but critical modules are substantially lower: live LLM model operations (35%), admin model/tool routes (38–43%), MCP executor (55%), MCP resolver (62%), and several vector database providers (37–46%).
- Migration runner/writer coverage is 16–17%, which is risky for one-way data operations.

**Required fix**

- Set per-package or per-module thresholds for security-sensitive and migration code rather than relying only on aggregate coverage.
- Prioritize authorization failures, upstream timeouts/malformed responses, secret redaction, partial batch failures, idempotency, and rollback/retry behavior.

**Done when**

- High-risk modules have explicit behavioral tests and agreed thresholds; aggregate coverage can no longer hide a nearly untested critical path.

### 9. Exercise integration and end-to-end suites in an isolated environment

**Evidence**

- This audit passed 1,302 non-integration backend tests but did not execute the 49 integration tests because they require provisioned database/external-service conditions.
- Playwright was not executed locally because it requires coordinated backend/frontend services and test accounts.

**Required fix**

- Provide a hermetic CI profile for Postgres-backed integration tests.
- Separate mocked contract tests from credentialed live-provider smoke tests.
- Schedule live-provider tests with restricted credentials, cost caps, timeouts, and clear skip/failure reporting.

**Done when**

- Every integration test is either run on PR, run on a documented schedule, or explicitly quarantined with an owner and expiry date.

### 10. Clear deprecations before the next framework major upgrades

**Evidence**

- The backend suite emits Pydantic class-config deprecations in `src/api/admin_users.py`.
- FastAPI status aliases used by application/tests are deprecated.

**Required fix**

- Replace class-based Pydantic config with `ConfigDict`.
- Replace deprecated HTTP status constants.
- Treat new application-owned deprecation warnings as CI failures while allowing narrowly documented third-party warnings.

## Verification baseline recorded by this audit

```text
Backend Ruff lint: passed
Backend Ruff format check: passed (338 files)
Backend Pyright strict: passed (0 errors, 0 warnings)
Backend non-integration tests: 1302 passed, 49 deselected
Backend coverage: 81%
Frontend TypeScript: passed
Frontend Vitest: 29 files / 173 tests passed
Frontend Next.js production build: passed
Frontend Prettier check: failed (102 files)
Frontend npm audit: 51 vulnerabilities (4 critical, 18 high, 27 moderate, 2 low)
Python pip-audit: 164 advisory matches in 20 packages
Integration tests: not run in this local audit
Playwright tests: not run in this local audit
```

## Recommended execution order

1. P0-1 internal-route fail-closed authentication.
2. P0-2 dependency triage and production-critical upgrades.
3. P1-3 frontend CI plus the isolated formatting cleanup.
4. P1-4 dependency/secret/container scanning.
5. P1-5 and P1-6 supply-chain/container hardening.
6. P1-7 browser headers.
7. P2 coverage, integration execution, and deprecation cleanup.
