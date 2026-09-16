# Azure SSO setup

**Audience:** platform ops/SRE. Covers Azure AD app registration and env-var mapping for
Composer's NextAuth v5 + backend SSO exchange integration.

> **Not currently enabled.** Composer supports Azure AD SSO as a standalone-mode auth option
> (`SSO_ENABLED=true`, shipped Phase 10), but the live deployment authenticates with Composer's own
> standalone JWT (email + password) — `SSO_ENABLED` is unset. This doc is the setup runbook for
> turning Azure SSO on if/when it's needed, not a description of how login currently works.

> **Deployment target: GCP Cloud Run, not Vercel.** Composer's actual production deployment runs
> both the frontend (`composer-frontend`) and the backend (`composer-backend`) as separate Cloud Run
> services — see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md). Every env var below goes on the
> relevant Cloud Run service via Secret Manager / `gcloud run services update --update-secrets`, not
> Vercel. Vercel is documented separately ([vercel-setup.md](vercel-setup.md)) only as an alternate
> path for the frontend; even on that path the FastAPI backend still needs Cloud Run (or another
> long-lived host), since it cannot run as a Vercel Serverless Function — so the backend env vars in
> §5 always go on Cloud Run regardless of which path the frontend takes.

## Prerequisites

- Azure AD tenant access (Global Admin or Application Administrator role).
- Composer backend deployed to a known URL (e.g., the `composer-backend` Cloud Run service's URL —
  see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)).
- Composer frontend deployed (e.g., the `composer-frontend` Cloud Run service's URL, or a Vercel
  deployment if you're on the alternate path).

---

## 1. Register an app in Azure AD

1. Open [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **App Registrations** → **New Registration**.
2. Fill in:
   - **Name:** `Composer (prod)` (or env-specific, e.g., `Composer (staging)`).
   - **Supported account types:** Select **Accounts in this organizational directory only (Single tenant)**.
   - **Redirect URI:** Select **Web** and paste: `https://composer.example.com/api/auth/callback/azure-ad`
     (Replace `composer.example.com` with your actual domain.)
3. Click **Register**. The app is created.

---

## 2. Note the IDs

On the app's **Overview** page, copy these values:

| Field | Purpose |
|---|---|
| **Application (client) ID** | Maps to `AZURE_AD_CLIENT_ID` (frontend NextAuth) and `SSO_AZURE_AD_EXPECTED_AUDIENCE` (backend). If you add an API scope (below), prefix it with `api://`. |
| **Directory (tenant) ID** | Maps to `AZURE_AD_TENANT_ID` (frontend) and `SSO_AZURE_AD_TENANT_ID` (backend). |

Store these securely; you'll paste them into the frontend and backend Cloud Run services' env vars/secrets.

---

## 3. Create a client secret

1. On the app → **Certificates & Secrets** → **Client Secrets** → **New Client Secret**.
2. Add a description (e.g., `Composer Production Deployment`).
3. Set Expires to **24 months** (or as your org's policy dictates).
4. Click **Add**.
5. **Copy the Value** (the secret itself, not the Secret ID). This appears only once.
6. Store it securely; it maps to `AZURE_AD_CLIENT_SECRET` on the `composer-frontend` Cloud Run service (or Vercel, if using the alternate frontend path).

> **Never commit the secret to source control.** Treat it as a password.

---

## 4. Configure API permissions

1. On the app → **API Permissions** → **Add a Permission** → **Microsoft Graph** (already highlighted).
2. Select **Delegated permissions** and add:
   - `openid`
   - `profile`
   - `email`
   - `User.Read`
3. Click **Add permissions**.
4. Back on **API Permissions**, click **Grant admin consent for [Your Tenant]** and confirm.

This allows the app to read the user's email and profile claims from Azure AD.

---

## 5. Backend environment variables

Set these as secrets on the `composer-backend` Cloud Run service (Secret Manager + `--update-secrets`, the same pattern every other backend secret uses — see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)'s "Rotate a secret" section):

| Variable | Value |
|---|---|
| `SSO_ENABLED` | `true` |
| `SSO_AZURE_AD_TENANT_ID` | Directory (tenant) ID from §2. |
| `SSO_AZURE_AD_CLIENT_ID` | Application (client) ID from §2. |
| `SSO_AZURE_AD_EXPECTED_AUDIENCE` | Same as `SSO_AZURE_AD_CLIENT_ID` (or `api://<client-id>` if you have an API scope). |

```powershell
gcloud run services update composer-backend `
  --region=us-central1 `
  --update-secrets=SSO_ENABLED=composer-sso-enabled:latest,SSO_AZURE_AD_TENANT_ID=composer-sso-tenant-id:latest,SSO_AZURE_AD_CLIENT_ID=composer-sso-client-id:latest,SSO_AZURE_AD_EXPECTED_AUDIENCE=composer-sso-audience:latest
```

(Create each Secret Manager secret first via `gcloud secrets create <name> --data-file=<path>` if it doesn't already exist — see gcp-cloud-run-setup.md's secret-creation steps.)

Verify the `/auth/sso-exchange` endpoint is working:

```bash
# Request an Azure AD token from a user's client (or use the browser console in /login)
# Then call, against the composer-backend Cloud Run URL:
curl -X POST https://<composer-backend-url>/auth/sso-exchange \
  -H "Content-Type: application/json" \
  -d '{"token": "<azure-ad-jwt>"}'
# Expected: 200 with a Composer JWT
```

---

## 6. Frontend environment variables

Set these as secrets on the `composer-frontend` Cloud Run service (or in Vercel → **Settings** →
**Environment Variables**, if using the alternate Vercel path from [vercel-setup.md](vercel-setup.md)):

| Variable | Value |
|---|---|
| `AZURE_AD_CLIENT_ID` | Application (client) ID from §2. |
| `AZURE_AD_TENANT_ID` | Directory (tenant) ID from §2. |
| `AZURE_AD_CLIENT_SECRET` | Client secret from §3. |
| `NEXT_PUBLIC_AZURE_SSO_ENABLED` | `true` |

```powershell
gcloud run services update composer-frontend `
  --region=us-central1 `
  --update-secrets=AZURE_AD_CLIENT_ID=composer-azure-client-id:latest,AZURE_AD_TENANT_ID=composer-azure-tenant-id:latest,AZURE_AD_CLIENT_SECRET=composer-azure-client-secret:latest,NEXT_PUBLIC_AZURE_SSO_ENABLED=composer-azure-sso-enabled:latest
```

`NEXT_PUBLIC_*` vars are baked in at build time, not read at runtime — after setting them, trigger a
rebuild + redeploy rather than just a config update: push to `main` (GitHub Actions rebuilds and
redeploys both services automatically — see gcp-cloud-run-setup.md's Step 5) or re-run
`.\scripts\gcp-bootstrap.ps1`. On the alternate Vercel path, the equivalent is `npx vercel --prod`.

The "Continue with Azure" button will now appear on the `/login` page.

---

## 7. Email claim configuration (if needed)

Azure AD doesn't always populate the `email` claim in the JWT. If you see errors like
"Azure JWT missing email claim," configure the token to include it:

1. On the app → **Token Configuration** → **Add optional claim**.
2. Select **Email** (under the "ID" column).
3. Click **Add**. Azure now includes the `email` claim in the JWT.

Composer's `/auth/sso-exchange` handles both cases — it falls back to `preferred_username`
if `email` is missing — but including the claim explicitly is cleaner.

---

## 8. Testing the flow

1. Navigate to `https://composer.example.com/login` (production URL).
2. Click **Continue with Azure AD**.
3. You are redirected to Azure's login → consent screen.
4. After consent, you are redirected back to Composer and logged in.
5. Verify: Go to `https://composer.example.com/auth/me` — you should see your user profile with `role`, `email`, etc.

If login fails:

- Check backend logs (`gcloud run services logs read composer-backend --region=us-central1` — see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)) for `/auth/sso-exchange` errors.
- Verify `SSO_ENABLED=true` and the three `SSO_AZURE_AD_*` env vars are set.
- Confirm the redirect URI in Azure AD matches exactly: `https://composer.example.com/api/auth/callback/azure-ad`.

---

## 9. Troubleshooting

### "Azure JWT missing email claim"

**Fix:** Add email to the Token Configuration (§7) and redeploy the frontend.

### "Invalid client secret" or "unauthorized_client"

**Cause:** The secret on the frontend Cloud Run service (or Vercel, on the alternate path) doesn't match Azure's copy, or it has expired.

**Fix:**
1. Rotate the secret in Azure (§3), copy the new Value.
2. Update the `composer-azure-client-secret` Secret Manager secret and re-run the `--update-secrets` command from §6 (or update it in Vercel, on the alternate path).
3. Trigger a redeploy.

### "Redirect URI mismatch"

**Cause:** The redirect URI in Azure AD does not match the one NextAuth sends.

**Fix:** Verify the redirect URI in Azure → App Registrations → (your app) → **Authentication** 
matches exactly: `https://composer.example.com/api/auth/callback/azure-ad`. Update if needed.

### User registers with Azure SSO but can't access their workflows

**Cause:** Data migrated from OAB with `user_id = NULL`; reconciliation not run.

**Fix:** See [admin-operations.md §3](admin-operations.md) — run `composer reconcile --email <their-email>`.

---

## Cross-references

- [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) — the actual deployment target for both frontend and backend; secret rotation, logs, redeploy procedure
- [vercel-setup.md](vercel-setup.md) — the alternate frontend-only deployment path, if you're not using Cloud Run for the frontend
- [admin-operations.md](admin-operations.md) — post-deploy admin tasks
- [monitoring.md](monitoring.md) — logging + debugging SSO flows
