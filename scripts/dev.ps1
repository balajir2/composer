# Composer dev launcher (Windows / PowerShell).
#
# Opens two new PowerShell windows:
#   - Backend:  uv run uvicorn src.main:app --reload --port $BackendPort
#   - Frontend: npm run dev (port 3000, default Next.js)
#
# Before launching it verifies:
#   - $BackendPort is free (no other dev server squatting on it)
#   - frontend\.env.local has NEXT_PUBLIC_COMPOSER_API_URL pointing at
#     http://localhost:$BackendPort - if it doesn't, the script offers to
#     fix it inline (otherwise the frontend would silently call the wrong
#     port and login would fail with "Sign-in failed. Check your credentials.")
#
# Usage:
#   .\scripts\dev.ps1                # backend on 8001 (default)
#   .\scripts\dev.ps1 -BackendPort 9000
#   .\scripts\dev.ps1 -SkipChecks    # don't validate env / port
#
# Stop both servers by closing each window or Ctrl-C inside it.

[CmdletBinding()]
param(
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 3000,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot 'frontend'
$frontendEnvFile = Join-Path $frontendDir '.env.local'

function Write-Header {
    param([string]$Text)
    Write-Host ''
    Write-Host ('=' * 60) -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ('=' * 60) -ForegroundColor Cyan
}

function Test-PortFree {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return -not $conn
}

function Get-PortHolder {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) {
        return "$($proc.ProcessName) (PID $($proc.Id))"
    }
    return "PID $($conn.OwningProcess)"
}

Write-Header "Composer dev launcher"
Write-Host "Repo:           $repoRoot"
Write-Host "Backend port:   $BackendPort"
Write-Host "Frontend port:  $FrontendPort"

if (-not $SkipChecks) {
    # 1. Backend port must be free.
    if (-not (Test-PortFree -Port $BackendPort)) {
        $holder = Get-PortHolder -Port $BackendPort
        Write-Host ''
        Write-Host "ERROR: Port $BackendPort is already in use by $holder." -ForegroundColor Red
        Write-Host "Free the port (close that process) or pass a different -BackendPort." -ForegroundColor Red
        exit 1
    }
    Write-Host "Port $BackendPort is free." -ForegroundColor Green

    # 2. frontend/.env.local should point at the backend port. If it doesn't,
    #    fix it inline so the frontend's NextAuth credentials provider hits
    #    the right URL.
    if (Test-Path $frontendEnvFile) {
        $envContent = Get-Content $frontendEnvFile -Raw
        $expected = "http://localhost:$BackendPort"
        if ($envContent -notmatch [regex]::Escape("NEXT_PUBLIC_COMPOSER_API_URL=$expected")) {
            Write-Host ''
            Write-Host "WARNING: frontend\.env.local NEXT_PUBLIC_COMPOSER_API_URL does not match $expected" -ForegroundColor Yellow
            $reply = Read-Host "Update it now? (y/N)"
            if ($reply -eq 'y' -or $reply -eq 'Y') {
                $newContent = $envContent -replace 'NEXT_PUBLIC_COMPOSER_API_URL=.*', "NEXT_PUBLIC_COMPOSER_API_URL=$expected"
                Set-Content -Path $frontendEnvFile -Value $newContent -NoNewline
                Write-Host "frontend\.env.local updated." -ForegroundColor Green
            } else {
                Write-Host "Skipped. Frontend may not reach the backend until you fix it manually." -ForegroundColor Yellow
            }
        } else {
            Write-Host "frontend\.env.local points at $expected." -ForegroundColor Green
        }
    } else {
        Write-Host "WARNING: frontend\.env.local not found - frontend may fail to start." -ForegroundColor Yellow
    }
}

# Backend command. Quoting handles spaces in the working directory.
$backendCmd = @"
Write-Host '======= Composer BACKEND =======' -ForegroundColor Cyan;
Write-Host 'http://localhost:$BackendPort/health' -ForegroundColor Green;
Write-Host '';
Set-Location '$repoRoot';
uv run uvicorn src.main:app --reload --port $BackendPort
"@

# Frontend command.
$frontendCmd = @"
Write-Host '======= Composer FRONTEND =======' -ForegroundColor Cyan;
Write-Host 'http://localhost:$FrontendPort' -ForegroundColor Green;
Write-Host '';
Set-Location '$frontendDir';
npm run dev
"@

Write-Header "Launching..."
Write-Host "Opening backend window..."
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoExit', '-Command', $backendCmd | Out-Null
Start-Sleep -Milliseconds 800
Write-Host "Opening frontend window..."
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoExit', '-Command', $frontendCmd | Out-Null

Write-Host ''
Write-Host "Two windows opened. Close them or Ctrl-C inside each to stop." -ForegroundColor Cyan
Write-Host "Sign in at: http://localhost:$FrontendPort" -ForegroundColor Cyan
