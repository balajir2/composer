# Composer — first-time GCP Cloud Run deploy helper.
#
# What it does (idempotent — safe to re-run):
#   1. Creates Secret Manager entries for every runtime secret Composer
#      needs.  Reads values from .env (repo root) by default; you can
#      override per-secret with -SkipSecrets.
#   1a. Provisions the composer-executions Cloud Tasks queue and a shared
#      OIDC service account (P1-2/P1-4, ADR-0033) used by both Cloud
#      Tasks (claim-and-run) and Cloud Scheduler (sweep) push auth.
#   2. Builds + pushes the backend image to Artifact Registry.
#   3. Deploys the backend Cloud Run service.
#   3a. Grants the backend's own runtime service account (Cloud Run's
#      default compute SA, since no --service-account is set on the
#      backend deploy) roles/cloudtasks.enqueuer plus
#      roles/iam.serviceAccountTokenCreator on the shared OIDC service
#      account, so enqueue_execution() can actually call Cloud Tasks'
#      CreateTask and mint the OIDC token embedded in the task body.
#   3b. Grants the shared service account roles/run.invoker on the
#      backend service.
#   3c. Provisions the composer-sweep Cloud Scheduler job, which POSTs
#      /internal/sweep every 5 minutes (replaces the in-process sweeper
#      loop in production; EXECUTION_SWEEPER_INTERVAL_SECONDS=0 disables
#      it on the deployed service).
#   4. Builds + pushes the frontend image with NEXT_PUBLIC_COMPOSER_API_URL
#      baked in (pointing at the backend URL — *.run.app for the first
#      deploy, custom domain when provided).
#   5. Deploys the frontend Cloud Run service.
#   6. Prints the final URLs.
#
# Run from the repo root.
#
# Usage:
#   .\scripts\gcp-bootstrap.ps1                              # uses *.run.app domains
#   .\scripts\gcp-bootstrap.ps1 -BackendDomain api.composer.example.com `
#                               -FrontendDomain composer.example.com
#   .\scripts\gcp-bootstrap.ps1 -SkipSecrets                 # don't re-push secrets
#   .\scripts\gcp-bootstrap.ps1 -SkipBuild                   # use the previously-built images

[CmdletBinding()]
param(
    [string]$ProjectId      = "composer-497608",
    [string]$Region         = "us-central1",
    [string]$ArtifactRepo   = "composer",
    [string]$BackendService = "composer-backend",
    [string]$FrontendService = "composer-frontend",
    [string]$BackendDomain  = "",         # optional custom domain, e.g. api.composer.example.com
    [string]$FrontendDomain = "",         # optional custom domain, e.g. composer.example.com
    [switch]$SkipSecrets,
    [switch]$SkipBuild,
    [switch]$AllowUnauthenticated = $true # Cloud Run public access; flip false for SSO-only deployments
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host ("─" * 60) -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ("─" * 60) -ForegroundColor Cyan
}

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $idx = $line.IndexOf('=')
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        $values[$key] = $value
    }
    return $values
}

function Set-Secret {
    param([string]$Name, [string]$Value)
    if (-not $Value) {
        Write-Host "  [skip] $Name (no value in .env)" -ForegroundColor DarkGray
        return
    }
    # Create the secret if it doesn't exist
    $exists = gcloud secrets describe $Name --project=$ProjectId 2>$null
    if (-not $exists) {
        Write-Host "  [create] $Name"
        $tmp = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmp, $Value)
            gcloud secrets create $Name --replication-policy=automatic --project=$ProjectId --data-file=$tmp | Out-Null
        } finally {
            Remove-Item $tmp -Force
        }
    } else {
        Write-Host "  [update] $Name"
        $tmp = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmp, $Value)
            gcloud secrets versions add $Name --data-file=$tmp --project=$ProjectId | Out-Null
        } finally {
            Remove-Item $tmp -Force
        }
    }
}

# ─── 0. sanity ──────────────────────────────────────────────────────
Write-Step "Composer GCP bootstrap"
Write-Host "Project:    $ProjectId"
Write-Host "Region:     $Region"
Write-Host "Repo root:  $repoRoot"

$activeProject = gcloud config get project 2>$null
if ($activeProject -ne $ProjectId) {
    Write-Host "Switching active gcloud project to $ProjectId" -ForegroundColor Yellow
    gcloud config set project $ProjectId | Out-Null
}

# Ensure docker is logged into Artifact Registry.
Write-Host "Configuring docker auth for Artifact Registry..."
gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet | Out-Null

$registryHost = "$Region-docker.pkg.dev"
$registryPath = "$registryHost/$ProjectId/$ArtifactRepo"
$backendImage  = "$registryPath/backend:latest"
$frontendImage = "$registryPath/frontend:latest"

# ─── 1. secrets ─────────────────────────────────────────────────────
if (-not $SkipSecrets) {
    Write-Step "1. Push secrets to Secret Manager"
    $envFile = Join-Path $repoRoot '.env'
    if (-not (Test-Path $envFile)) {
        Write-Host "ERROR: $envFile not found.  Create it with the required values (see .env.example)." -ForegroundColor Red
        exit 1
    }
    $env = Read-EnvFile -Path $envFile

    # Backend secrets — every value lives in Secret Manager, referenced by
    # the Cloud Run service deploy step below.
    Set-Secret -Name "composer-database-url"     -Value $env["DATABASE_URL"]
    Set-Secret -Name "composer-jwt-secret"       -Value $env["JWT_SECRET"]
    Set-Secret -Name "composer-encryption-key"   -Value $env["ENCRYPTION_KEY"]
    Set-Secret -Name "composer-anthropic-key"    -Value $env["ANTHROPIC_API_KEY"]
    Set-Secret -Name "composer-openai-key"       -Value $env["OPENAI_API_KEY"]
    Set-Secret -Name "composer-google-key"       -Value $env["GOOGLE_API_KEY"]
    Set-Secret -Name "composer-groq-key"         -Value $env["GROQ_API_KEY"]
    Set-Secret -Name "composer-tavily-key"       -Value $env["TAVILY_API_KEY"]
    Set-Secret -Name "composer-firecrawl-key"    -Value $env["FIRECRAWL_API_KEY"]
    Set-Secret -Name "composer-langchain-key"    -Value $env["LANGCHAIN_API_KEY"]
    Set-Secret -Name "composer-nextauth-secret"  -Value $env["NEXTAUTH_SECRET"]
}

# ─── 1a. Cloud Tasks queue + shared OIDC service account ────────────
# P1-2/P1-4 durable execution (ADR-0033). One shared service account
# authenticates BOTH Cloud Tasks push deliveries (claim-and-run) and
# Cloud Scheduler push deliveries (sweep) — src/api/internal.py's
# _verify_internal_oidc derives the expected audience from the
# request's own path, specifically so one identity/verification
# mechanism can serve both endpoints. Do not provision two accounts.
Write-Step "1a. Cloud Tasks queue + shared OIDC service account"

$queueExists = gcloud tasks queues describe composer-executions `
    --location=$Region --project=$ProjectId 2>$null
if (-not $queueExists) {
    Write-Host "  [create] Cloud Tasks queue composer-executions"
    gcloud tasks queues create composer-executions `
        --location=$Region `
        --project=$ProjectId `
        --max-attempts=5 `
        --max-retry-duration=3600s `
        --min-backoff=10s `
        --max-backoff=300s | Out-Null
} else {
    Write-Host "  [exists] Cloud Tasks queue composer-executions" -ForegroundColor DarkGray
}

$cloudTasksSvcAccountId = "composer-tasks"
$cloudTasksServiceAccountEmail = "$cloudTasksSvcAccountId@$ProjectId.iam.gserviceaccount.com"
$svcAccountExists = gcloud iam service-accounts describe $cloudTasksServiceAccountEmail `
    --project=$ProjectId 2>$null
if (-not $svcAccountExists) {
    Write-Host "  [create] $cloudTasksServiceAccountEmail"
    gcloud iam service-accounts create $cloudTasksSvcAccountId `
        --project=$ProjectId `
        --display-name="Composer Cloud Tasks / Scheduler" | Out-Null
} else {
    Write-Host "  [exists] $cloudTasksServiceAccountEmail" -ForegroundColor DarkGray
}

# add-iam-policy-binding is itself idempotent (no duplicate binding is
# created on re-run), so this isn't wrapped in an existence check like
# the create steps above.
# --condition=None avoids an interactive IAM-conditions prompt when this
# script is run non-interactively.
Write-Host "  [grant] roles/cloudtasks.enqueuer -> $cloudTasksServiceAccountEmail"
gcloud projects add-iam-policy-binding $ProjectId `
    --member="serviceAccount:$cloudTasksServiceAccountEmail" `
    --role="roles/cloudtasks.enqueuer" `
    --condition=None | Out-Null

# NOTE: granting roles/cloudtasks.enqueuer to $cloudTasksServiceAccountEmail
# above does NOT, by itself, let enqueue_execution() work. The backend
# Cloud Run service's OWN runtime identity (Cloud Run's default compute
# SA — this script deliberately does not deploy the backend with
# --service-account=$cloudTasksServiceAccountEmail) is what actually calls
# CloudTasksAsyncClient.create_task() via Application Default Credentials.
# That identity is granted its own roles/cloudtasks.enqueuer, plus
# roles/iam.serviceAccountTokenCreator on $cloudTasksServiceAccountEmail
# (to mint the OIDC token embedded in the task body), in section 3a below
# — deferred until after the backend is deployed, since only then can its
# runtime SA be queried. See section 3a's own comment for the full
# rationale, including why the backend isn't just deployed with
# --service-account=$cloudTasksServiceAccountEmail directly.

# ─── 2. build + push backend image ──────────────────────────────────
if (-not $SkipBuild) {
    Write-Step "2. Build + push backend image"
    Push-Location $repoRoot
    try {
        docker build -t $backendImage -f Dockerfile .
        docker push $backendImage
    } finally {
        Pop-Location
    }
}

# ─── 3. deploy backend service ──────────────────────────────────────
Write-Step "3. Deploy backend Cloud Run service"

# Build the --set-secrets flag: only include secrets that actually exist.
# `gcloud run deploy` will fail if any referenced secret doesn't exist.
$availableSecrets = (gcloud secrets list --project=$ProjectId --format="value(name)" 2>$null) -split "`n" | Where-Object { $_ }
$secretMappings = @(
    @{ env = "DATABASE_URL";      secret = "composer-database-url" },
    @{ env = "JWT_SECRET";        secret = "composer-jwt-secret" },
    @{ env = "ENCRYPTION_KEY";    secret = "composer-encryption-key" },
    @{ env = "ANTHROPIC_API_KEY"; secret = "composer-anthropic-key" },
    @{ env = "OPENAI_API_KEY";    secret = "composer-openai-key" },
    @{ env = "GOOGLE_API_KEY";    secret = "composer-google-key" },
    @{ env = "GROQ_API_KEY";      secret = "composer-groq-key" },
    @{ env = "TAVILY_API_KEY";    secret = "composer-tavily-key" },
    @{ env = "FIRECRAWL_API_KEY"; secret = "composer-firecrawl-key" },
    @{ env = "LANGCHAIN_API_KEY"; secret = "composer-langchain-key" }
)
$setSecretsParts = $secretMappings |
    Where-Object { $availableSecrets -contains $_.secret } |
    ForEach-Object { "$($_.env)=$($_.secret):latest" }
$setSecrets = $setSecretsParts -join ","

# CPU throttling is left at Cloud Run's default (throttled outside of a
# live request) — deliberately NOT --no-cpu-throttling. That flag existed
# solely to keep the in-process stuck-execution sweeper's asyncio loop
# ticking between requests, but bills CPU continuously for as long as an
# instance is up regardless of request activity — the dominant Cloud Run
# cost driver per a 2026-07-14 cost review (ADR-0033 addendum). Task 11/14
# replace the in-process loop with the Cloud Scheduler-triggered
# composer-sweep job (section 3c below), which POSTs /internal/sweep on a
# fixed interval as a real inbound request — no background CPU allocation
# needed. Do not re-add --no-cpu-throttling.
$backendDeployArgs = @(
    "run", "deploy", $BackendService,
    "--image=$backendImage",
    "--region=$Region",
    "--project=$ProjectId",
    "--platform=managed",
    "--cpu=1",
    "--memory=1Gi",
    "--min-instances=0",
    "--max-instances=3",
    "--cpu-boost",
    "--timeout=3600",               # 60-minute request timeout (Cloud Run max) — covers long workflows
    "--concurrency=80",
    "--port=8080",
    # EXECUTION_SWEEPER_INTERVAL_SECONDS=0 disables the in-process sweeper
    # loop in production (src/config.py: "Set 0 to disable") now that
    # composer-sweep (section 3c below) covers lease/retention cleanup via
    # a real inbound request. The loop itself stays in the codebase for
    # local/dev use (Task 11) — this only turns it off in this deployment.
    # GCP_PROJECT_ID/GCP_REGION/CLOUD_TASKS_QUEUE/CLOUD_TASKS_SERVICE_ACCOUNT
    # are what enqueue_execution() (src/execution/cloud_tasks.py) and
    # _verify_internal_oidc() (src/api/internal.py) need at runtime to
    # enqueue tasks and verify push-auth tokens, respectively.
    "--set-env-vars=ENVIRONMENT=production,COMPOSER_DEPLOYMENT_MODE=standalone,LOG_LEVEL=INFO,EXECUTION_STUCK_AFTER_SECONDS=900,EXECUTION_SWEEPER_INTERVAL_SECONDS=0,GCP_PROJECT_ID=$ProjectId,GCP_REGION=$Region,CLOUD_TASKS_QUEUE=composer-executions,CLOUD_TASKS_SERVICE_ACCOUNT=$cloudTasksServiceAccountEmail",
    "--set-secrets=$setSecrets"
)
if ($AllowUnauthenticated) { $backendDeployArgs += "--allow-unauthenticated" }
gcloud @backendDeployArgs

$backendRunUrl = gcloud run services describe $BackendService --region=$Region --project=$ProjectId --format="value(status.url)"
Write-Host ""
Write-Host "Backend deployed: $backendRunUrl" -ForegroundColor Green

# ─── 3a. grant backend runtime SA permission to enqueue Cloud Tasks ─
# enqueue_execution() (src/execution/cloud_tasks.py) constructs
# CloudTasksAsyncClient with no explicit credentials, so it resolves
# Application Default Credentials — inside Cloud Run, that's whatever
# identity THIS service actually runs as. Since section 3 above
# deliberately does not deploy $BackendService with
# --service-account=$cloudTasksServiceAccountEmail (doing so would also
# change which identity reads the --set-secrets-mounted secrets, which
# would need roles/secretmanager.secretAccessor granted before the
# service could even start — an untested, high-blast-radius change out
# of scope here), the backend runs as Cloud Run's default compute
# service account instead. That identity — NOT
# $cloudTasksServiceAccountEmail — is the one that calls
# CreateTask(), and it was never granted permission to do so. It also
# needs to mint the OIDC token embedded in the task body
# (oidc_token.service_account_email = $cloudTasksServiceAccountEmail),
# which requires the actAs permission on that service account
# specifically, not a project-level role.
#
# Queried directly from the deployed service (rather than guessing the
# "<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" format) so
# this is correct even if the default was ever overridden. Falls back to
# the constructed default-compute-SA email only if the service
# description doesn't echo an explicit value back (observed to happen
# when --service-account was never passed at deploy time).
Write-Step "3a. Grant backend runtime SA permission to enqueue Cloud Tasks"
$backendRuntimeSa = gcloud run services describe $BackendService `
    --region=$Region --project=$ProjectId `
    --format="value(spec.template.spec.serviceAccountName)"
if (-not $backendRuntimeSa) {
    Write-Host "  Runtime SA not explicit on the service spec; falling back to the default compute SA." -ForegroundColor DarkGray
    $projectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
    if (-not $projectNumber) {
        Write-Host "ERROR: could not determine $BackendService's runtime service account (describe returned empty, and the project-number fallback lookup also failed)." -ForegroundColor Red
        exit 1
    }
    $backendRuntimeSa = "$projectNumber-compute@developer.gserviceaccount.com"
}
Write-Host "  Backend runtime SA: $backendRuntimeSa"

# add-iam-policy-binding is itself idempotent (safe to re-run; matches
# the style used for $cloudTasksServiceAccountEmail's enqueuer grant
# above in section 1a).
Write-Host "  [grant] roles/cloudtasks.enqueuer -> $backendRuntimeSa"
gcloud projects add-iam-policy-binding $ProjectId `
    --member="serviceAccount:$backendRuntimeSa" `
    --role="roles/cloudtasks.enqueuer" `
    --condition=None | Out-Null

# This is a binding ON $cloudTasksServiceAccountEmail's own IAM policy
# (service-accounts add-iam-policy-binding), granting $backendRuntimeSa
# the right to act as it — NOT a project-level role grant TO
# $cloudTasksServiceAccountEmail. Those are different commands; this is
# the one that lets the backend mint an OIDC token asserting the
# composer-tasks identity.
Write-Host "  [grant] roles/iam.serviceAccountTokenCreator -> $backendRuntimeSa on $cloudTasksServiceAccountEmail"
gcloud iam service-accounts add-iam-policy-binding $cloudTasksServiceAccountEmail `
    --member="serviceAccount:$backendRuntimeSa" `
    --role="roles/iam.serviceAccountTokenCreator" `
    --project=$ProjectId | Out-Null

# ─── 3b. grant Cloud Run invoker to the shared service account ──────
# Lets both Cloud Tasks (claim-and-run) and Cloud Scheduler (sweep) push
# requests reach the backend under this identity. Cloud Run's own IAM
# check is bypassed while -AllowUnauthenticated stays true (the default),
# but this binding matters the moment that's flipped false, and costs
# nothing to grant now.
Write-Step "3b. Grant Cloud Run invoker to shared service account"
gcloud run services add-iam-policy-binding $BackendService `
    --region=$Region `
    --project=$ProjectId `
    --member="serviceAccount:$cloudTasksServiceAccountEmail" `
    --role="roles/run.invoker" | Out-Null

# ─── 4. build + push frontend image ─────────────────────────────────
# The frontend needs NEXT_PUBLIC_COMPOSER_API_URL baked at build time.
# If a custom backend domain was passed, point at it; otherwise the
# *.run.app URL works fine.
$apiUrl = if ($BackendDomain) { "https://$BackendDomain" } else { $backendRunUrl }
Write-Host "Frontend will call API at: $apiUrl" -ForegroundColor Yellow

# BACKEND_PUBLIC_URL is used server-side (src/engine/approval_email.py) to
# build the links inside emailed approve/reject notifications. It defaults
# to http://localhost:8000 (src/config.py) when unset, so without this the
# app looks fully deployed but every approval email silently links back to
# whichever machine happens to be running a local dev server.
Write-Host "Setting BACKEND_PUBLIC_URL=$apiUrl on $BackendService"
gcloud run services update $BackendService `
    --region=$Region `
    --project=$ProjectId `
    --update-env-vars="BACKEND_PUBLIC_URL=$apiUrl" | Out-Null

# ─── 3c. Cloud Scheduler sweep job ───────────────────────────────────
# Triggers POST /internal/sweep on a fixed interval, replacing the
# in-process sweeper loop (EXECUTION_SWEEPER_INTERVAL_SECONDS=0 above).
# Schedule matches EXECUTION_SWEEPER_INTERVAL_SECONDS's un-disabled
# default of 300s (src/config.py) — keep these in sync if that default
# ever changes. Placed here (after BACKEND_PUBLIC_URL is set from $apiUrl,
# not earlier from $backendRunUrl) so --oidc-token-audience exactly
# matches what src/api/internal.py's _verify_internal_oidc computes at
# request time: f"{settings.backend_public_url}{request.url.path}". Using
# $backendRunUrl instead would silently break auth whenever -BackendDomain
# is set, since backend_public_url would then be the custom domain, not
# the *.run.app URL.
Write-Step "3c. Cloud Scheduler sweep job"
$schedulerJobExists = gcloud scheduler jobs describe composer-sweep `
    --location=$Region --project=$ProjectId 2>$null
if (-not $schedulerJobExists) {
    Write-Host "  [create] Cloud Scheduler job composer-sweep"
    gcloud scheduler jobs create http composer-sweep `
        --location=$Region `
        --project=$ProjectId `
        --schedule="*/5 * * * *" `
        --uri="$apiUrl/internal/sweep" `
        --http-method=POST `
        --oidc-service-account-email="$cloudTasksServiceAccountEmail" `
        --oidc-token-audience="$apiUrl/internal/sweep" | Out-Null
} else {
    Write-Host "  [exists] Cloud Scheduler job composer-sweep" -ForegroundColor DarkGray
}

# ─── 3d. Google Drive file-trigger poll job (NOT yet provisioned) ───
# Google Drive OAuth + file-trigger polling (2026-07-16, Task 10 of the
# google-drive-oauth-file-trigger plan). Mirrors the composer-sweep job
# immediately above (section 3c): same shared OIDC service account
# ($cloudTasksServiceAccountEmail), same push-auth verification path
# (_verify_internal_oidc derives the expected audience from the
# request's own path — src/api/internal.py — so no new identity or
# verification mechanism is needed here, same rationale as section 1a's
# comment on why Cloud Tasks and Cloud Scheduler share one account).
#
# This script does NOT create this job. It is intentionally left
# commented out below and must be run manually, once:
#   1. GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
#      GOOGLE_PICKER_API_KEY are set in the deployed backend's env
#      (add them to Secret Manager + the $secretMappings /
#      --set-secrets wiring in section 3 above, the same way the other
#      composer-* secrets are handled, then redeploy the backend so the
#      OAuth callback route has real credentials).
#   2. Task 10's manual GCP Console steps are done: Drive API enabled,
#      OAuth consent screen configured (External, Testing, test users
#      added), OAuth 2.0 Client ID created (redirect URI
#      "$apiUrl/cloud-storage/google-drive/callback"), and a
#      Picker-API-restricted API key created.
#
# Do not uncomment and run this as part of an unattended script pass —
# treat it as a standalone manual step requiring its own confirmation:
#
# gcloud scheduler jobs create http composer-poll-file-triggers `
#     --location=$Region `
#     --project=$ProjectId `
#     --schedule="*/5 * * * *" `
#     --uri="$apiUrl/internal/poll-file-triggers" `
#     --http-method=POST `
#     --oidc-service-account-email="$cloudTasksServiceAccountEmail" `
#     --oidc-token-audience="$apiUrl/internal/poll-file-triggers"

if (-not $SkipBuild) {
    Write-Step "4. Build + push frontend image"
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        docker build -t $frontendImage `
            --build-arg NEXT_PUBLIC_COMPOSER_API_URL=$apiUrl `
            -f Dockerfile .
        docker push $frontendImage
    } finally {
        Pop-Location
    }
}

# ─── 5. deploy frontend service ─────────────────────────────────────
Write-Step "5. Deploy frontend Cloud Run service"

$frontendDeployArgs = @(
    "run", "deploy", $FrontendService,
    "--image=$frontendImage",
    "--region=$Region",
    "--project=$ProjectId",
    "--platform=managed",
    "--cpu=1",
    "--memory=512Mi",
    "--min-instances=0",
    "--max-instances=3",
    "--cpu-boost",
    "--timeout=60",
    "--concurrency=80",
    "--port=8080",
    "--set-secrets=NEXTAUTH_SECRET=composer-nextauth-secret:latest"
)
if ($AllowUnauthenticated) { $frontendDeployArgs += "--allow-unauthenticated" }

# NEXTAUTH_URL: point at the eventual customer-facing URL (custom domain
# if provided, otherwise the *.run.app URL).  Frontend rebuilds anyway
# when this changes.
$nextauthUrl = if ($FrontendDomain) { "https://$FrontendDomain" } else { $null }

# Two-step env-var assembly: --set-env-vars accepts comma-sep, but commas
# inside a single value break it.  We use --update-env-vars to be safe.
gcloud @frontendDeployArgs --set-env-vars="NODE_ENV=production"

if ($nextauthUrl) {
    gcloud run services update $FrontendService --region=$Region --project=$ProjectId `
        --update-env-vars="NEXTAUTH_URL=$nextauthUrl" | Out-Null
}

$frontendRunUrl = gcloud run services describe $FrontendService --region=$Region --project=$ProjectId --format="value(status.url)"

# ─── 6. update backend CORS allowlist ───────────────────────────────
# The backend's CORS middleware only allows the origins listed in
# COMPOSER_FRONTEND_ORIGINS in production-standalone mode.  We can only set
# this once the frontend URL is known, so it's a post-deploy update step.
Write-Step "6. Update backend CORS allowlist"
$frontendOrigins = @($frontendRunUrl)
if ($FrontendDomain) {
    $customOrigin = "https://$FrontendDomain"
    if ($frontendOrigins -notcontains $customOrigin) { $frontendOrigins += $customOrigin }
}
$originsCsv = ($frontendOrigins -join ",")
Write-Host "Setting COMPOSER_FRONTEND_ORIGINS=$originsCsv on $BackendService"
gcloud run services update $BackendService `
    --region=$Region `
    --project=$ProjectId `
    --update-env-vars="COMPOSER_FRONTEND_ORIGINS=$originsCsv" | Out-Null

# FRONTEND_URL is used server-side (src/api/approval_email.py's _redirect)
# to send reviewers somewhere sane after an emailed approve/reject link
# resolves. Defaults to http://localhost:3000 (src/config.py) when unset.
$frontendPublicUrl = if ($FrontendDomain) { "https://$FrontendDomain" } else { $frontendRunUrl }
Write-Host "Setting FRONTEND_URL=$frontendPublicUrl on $BackendService"
gcloud run services update $BackendService `
    --region=$Region `
    --project=$ProjectId `
    --update-env-vars="FRONTEND_URL=$frontendPublicUrl" | Out-Null

# ─── 7. summary ─────────────────────────────────────────────────────
Write-Step "Done"
Write-Host "Backend  → $backendRunUrl" -ForegroundColor Green
Write-Host "Frontend → $frontendRunUrl" -ForegroundColor Green
Write-Host ""
if ($BackendDomain -or $FrontendDomain) {
    Write-Host "Custom domain step:" -ForegroundColor Yellow
    if ($BackendDomain) {
        Write-Host "  gcloud run domain-mappings create --service=$BackendService --domain=$BackendDomain --region=$Region"
    }
    if ($FrontendDomain) {
        Write-Host "  gcloud run domain-mappings create --service=$FrontendService --domain=$FrontendDomain --region=$Region"
    }
    Write-Host "  Then add the DNS records gcloud prints."
}
Write-Host ""
Write-Host "Smoke test:" -ForegroundColor Yellow
Write-Host "  curl $backendRunUrl/health"
Write-Host "  open $frontendRunUrl"
