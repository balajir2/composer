# Composer — first-time GCP Cloud Run deploy helper.
#
# What it does (idempotent — safe to re-run):
#   1. Creates Secret Manager entries for every runtime secret Composer
#      needs.  Reads values from .env (repo root) by default; you can
#      override per-secret with -SkipSecrets.
#   2. Builds + pushes the backend image to Artifact Registry.
#   3. Deploys the backend Cloud Run service.
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
    "--no-cpu-throttling",          # CPU always allocated — needed for the background sweeper to tick
    "--timeout=3600",               # 60-minute request timeout (Cloud Run max) — covers long workflows
    "--concurrency=80",
    "--port=8080",
    "--set-env-vars=ENVIRONMENT=production,COMPOSER_DEPLOYMENT_MODE=standalone,LOG_LEVEL=INFO,EXECUTION_STUCK_AFTER_SECONDS=900,EXECUTION_SWEEPER_INTERVAL_SECONDS=300",
    "--set-secrets=$setSecrets"
)
if ($AllowUnauthenticated) { $backendDeployArgs += "--allow-unauthenticated" }
gcloud @backendDeployArgs

$backendRunUrl = gcloud run services describe $BackendService --region=$Region --project=$ProjectId --format="value(status.url)"
Write-Host ""
Write-Host "Backend deployed: $backendRunUrl" -ForegroundColor Green

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
