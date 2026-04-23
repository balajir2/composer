# Azure SSO setup

**Audience:** Bounteous ops/SRE. Covers Azure AD app registration and env-var mapping for
Composer's NextAuth v5 + backend SSO exchange integration.

---

## Prerequisites

- Azure AD tenant access (Global Admin or Application Administrator role).
- Composer backend deployed to a known URL (e.g., `https://composer.bounteous.com/api`).
- Composer frontend deployed (e.g., `https://composer.bounteous.com`).

---

## 1. Register an app in Azure AD

1. Open [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **App Registrations** → **New Registration**.
2. Fill in:
   - **Name:** `Composer (prod)` (or env-specific, e.g., `Composer (staging)`).
   - **Supported account types:** Select **Accounts in this organizational directory only (Single tenant)**.
   - **Redirect URI:** Select **Web** and paste: `https://composer.bounteous.com/api/auth/callback/azure-ad`
     (Replace `composer.bounteous.com` with your actual domain.)
3. Click **Register**. The app is created.

---

## 2. Note the IDs

On the app's **Overview** page, copy these values:

| Field | Purpose |
|---|---|
| **Application (client) ID** | Maps to `AZURE_AD_CLIENT_ID` (frontend NextAuth) and `SSO_AZURE_AD_EXPECTED_AUDIENCE` (backend). If you add an API scope (below), prefix it with `api://`. |
| **Directory (tenant) ID** | Maps to `AZURE_AD_TENANT_ID` (frontend) and `SSO_AZURE_AD_TENANT_ID` (backend). |

Store these securely; you'll paste them into Vercel and backend env vars.

---

## 3. Create a client secret

1. On the app → **Certificates & Secrets** → **Client Secrets** → **New Client Secret**.
2. Add a description (e.g., `Composer Production Deployment`).
3. Set Expires to **24 months** (or as your org's policy dictates).
4. Click **Add**.
5. **Copy the Value** (the secret itself, not the Secret ID). This appears only once.
6. Store it securely; it maps to `AZURE_AD_CLIENT_SECRET` in Vercel (frontend env).

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

Set these in your backend deployment (Vercel or a separate container):

| Variable | Value |
|---|---|
| `SSO_ENABLED` | `true` |
| `SSO_AZURE_AD_TENANT_ID` | Directory (tenant) ID from §2. |
| `SSO_AZURE_AD_CLIENT_ID` | Application (client) ID from §2. |
| `SSO_AZURE_AD_EXPECTED_AUDIENCE` | Same as `SSO_AZURE_AD_CLIENT_ID` (or `api://<client-id>` if you have an API scope). |

Verify the `/auth/sso-exchange` endpoint is working:

```bash
# Request an Azure AD token from a user's client (or use the browser console in /login)
# Then call:
curl -X POST https://composer.bounteous.com/api/auth/sso-exchange \
  -H "Content-Type: application/json" \
  -d '{"token": "<azure-ad-jwt>"}'
# Expected: 200 with a Composer JWT
```

---

## 6. Frontend environment variables (Vercel)

Set these in Vercel → **Settings** → **Environment Variables** (Production + Preview):

| Variable | Value |
|---|---|
| `AZURE_AD_CLIENT_ID` | Application (client) ID from §2. |
| `AZURE_AD_TENANT_ID` | Directory (tenant) ID from §2. |
| `AZURE_AD_CLIENT_SECRET` | Client secret from §3. |
| `NEXT_PUBLIC_AZURE_SSO_ENABLED` | `true` |

After setting these, trigger a redeploy:

```bash
npx vercel --prod
```

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

1. Navigate to `https://composer.bounteous.com/login` (production URL).
2. Click **Continue with Azure AD**.
3. You are redirected to Azure's login → consent screen.
4. After consent, you are redirected back to Composer and logged in.
5. Verify: Go to `https://composer.bounteous.com/auth/me` — you should see your user profile with `role`, `email`, etc.

If login fails:

- Check backend logs (`vercel logs` or container logs) for `/auth/sso-exchange` errors.
- Verify `SSO_ENABLED=true` and the three `SSO_AZURE_AD_*` env vars are set.
- Confirm the redirect URI in Azure AD matches exactly: `https://composer.bounteous.com/api/auth/callback/azure-ad`.

---

## 9. Troubleshooting

### "Azure JWT missing email claim"

**Fix:** Add email to the Token Configuration (§7) and redeploy the frontend.

### "Invalid client secret" or "unauthorized_client"

**Cause:** The secret in Vercel doesn't match Azure's copy, or it has expired.

**Fix:**
1. Rotate the secret in Azure (§3), copy the new Value.
2. Update `AZURE_AD_CLIENT_SECRET` in Vercel.
3. Trigger a redeploy.

### "Redirect URI mismatch"

**Cause:** The redirect URI in Azure AD does not match the one NextAuth sends.

**Fix:** Verify the redirect URI in Azure → App Registrations → (your app) → **Authentication** 
matches exactly: `https://composer.bounteous.com/api/auth/callback/azure-ad`. Update if needed.

### User registers with Azure SSO but can't access their workflows

**Cause:** Data migrated from OAB with `user_id = NULL`; reconciliation not run.

**Fix:** See [admin-operations.md §3](admin-operations.md) — run `composer reconcile --email <their-email>`.

---

## Cross-references

- [vercel-setup.md](vercel-setup.md) — frontend deployment + redeploy procedure
- [admin-operations.md](admin-operations.md) — post-deploy admin tasks
- [monitoring.md](monitoring.md) — logging + debugging SSO flows
