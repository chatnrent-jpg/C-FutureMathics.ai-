# NUKE-AND-RESTORE.ps1
# One-pass local fix for VettedME UHRS on Windows.
#
# Run from ANY PowerShell window:
#   cd "C:\Users\Henry Okojie\Documents\vettedme-backend"
#   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin/NUKE-AND-RESTORE.ps1" -OutFile .\NUKE-AND-RESTORE.ps1
#   powershell -ExecutionPolicy Bypass -File .\NUKE-AND-RESTORE.ps1
#
# Optional:
#   .\NUKE-AND-RESTORE.ps1 -Email "you@example.com" -Password "Practice123!" -SkipApiStart

param(
  [string]$Repo = (Get-Location).Path,
  [string]$Email = "chatnrent@gmail.com",
  [string]$Password = "Practice123!",
  [switch]$SkipApiStart
)

$ErrorActionPreference = "Stop"
$Base = "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin"

function Write-Step([string]$msg) {
  Write-Host ""
  Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Get-DropinFile([string]$rel) {
  $out = Join-Path $Repo ($rel -replace "/", "\")
  $dir = Split-Path $out -Parent
  if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $url = "$Base/$rel"
  Write-Host "GET $rel"
  Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
  if (-not (Test-Path $out) -or (Get-Item $out).Length -lt 20) {
    throw "Download failed or empty: $rel"
  }
}

Set-Location $Repo
if (-not (Test-Path ".\src\index.ts")) {
  throw "Not in vettedme-backend (src\index.ts missing). cd to Documents\vettedme-backend first."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker not found. Start Docker Desktop first."
}

Write-Step "1) Clear stale shell DATABASE_URL"
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

Write-Step "2) Write .env (API :8080, DB :5433)"
@"
DATABASE_URL=postgresql://vetted_user:vetted_password@127.0.0.1:5433/vetted_db?schema=public
NODE_ENV=development
PORT=8080
JWT_SECRET=dev-local-jwt-secret-change-me
CORS_ORIGIN=http://localhost:3000
ENCRYPTION_KEY=0123456789abcdef0123456789abcdef
RATE_LIMIT_MAX_REQUESTS=5000
"@ | Set-Content -Encoding ascii -Path ".\.env"
Get-Content ".\.env" | Select-String "DATABASE_URL|PORT="

Write-Step "3) Stop conflicting Postgres containers"
# IMPORTANT: use cmd /c so missing containers do not abort PowerShell Stop mode
foreach ($name in @("vetted-pg", "vetted-postgres")) {
  cmd /c "docker inspect $name >nul 2>&1"
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Stopping $name"
    cmd /c "docker stop $name >nul 2>&1"
    cmd /c "docker rm $name >nul 2>&1"
  } else {
    Write-Host "Skip $name (not present)"
  }
}

Write-Step "4) Fetch docker-compose.yml (host port 5433)"
Get-DropinFile "docker-compose.yml"

Write-Step "5) Compose down -v and start postgres only"
cmd /c "docker compose down -v"
cmd /c "docker compose up -d postgres"
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Step "6) Wait until Postgres is healthy"
$deadline = (Get-Date).AddSeconds(120)
$ready = $false
while ((Get-Date) -lt $deadline) {
  $names = docker ps --filter "name=vetted-postgres" --format "{{.Names}}"
  if (-not $names) {
    Write-Host "container not up yet..."
    Start-Sleep -Seconds 3
    continue
  }
  $health = cmd /c "docker inspect --format {{.State.Health.Status}} vetted-postgres 2>nul"
  if (-not $health) { $health = "none" }
  $health = $health.Trim()
  cmd /c "docker exec vetted-postgres pg_isready -U vetted_user -d vetted_db >nul 2>&1"
  if ($LASTEXITCODE -eq 0 -and ($health -eq "healthy" -or $health -eq "none")) {
    # accept healthy, or none if healthcheck lagging but pg_isready ok
    if ($health -eq "healthy" -or $LASTEXITCODE -eq 0) {
      docker exec vetted-postgres pg_isready -U vetted_user -d vetted_db 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) {
        Write-Host "OK Postgres ready (health=$health)"
        $ready = $true
        break
      }
    }
  }
  Write-Host "waiting... health=$health"
  Start-Sleep -Seconds 3
}
if (-not $ready) { throw "Postgres did not become ready in time" }

docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"

Write-Step "7) Sync critical source files from GitHub"
$files = @(
  "src/env.ts",
  "src/index.ts",
  "src/routes/auth.routes.ts",
  "src/middleware/errorHandler.ts",
  "src/modules/rlhf-core-rubric/controller.ts",
  "src/modules/rlhf-core-rubric/routes.ts",
  "src/modules/rlhf-core-rubric/validation.ts",
  "src/modules/rlhf-core-rubric/uhrsService.ts",
  "src/modules/rlhf-core-rubric/uhrsController.ts",
  "src/modules/rlhf-core-rubric/uhrsRoutes.ts",
  "prisma/schema.prisma",
  "scripts/check-db.ts",
  "scripts/create-practice-user.ts",
  "frontend/src/lib/uhrsApi.ts",
  "frontend/src/lib/rlhfApi.ts",
  "frontend/src/app/uhrs/page.tsx",
  "frontend/src/app/login/page.tsx",
  "frontend/src/app/page.tsx"
)
foreach ($f in $files) { Get-DropinFile $f }

# Prove key markers
$idx = Get-Content ".\src\index.ts" -Raw
if ($idx -notmatch "uhrsRoutes") { throw "src\index.ts missing uhrsRoutes after sync" }
if ($idx -notmatch "UHRS simulator MOUNTED") { throw "src\index.ts missing UHRS boot banner" }
$ctl = Get-Content ".\src\modules\rlhf-core-rubric\controller.ts" -Raw
if ($ctl -notmatch "getLiveAnalyticsFeed") { throw "controller.ts missing getLiveAnalyticsFeed" }
Write-Host "OK: index + controller markers present"

Write-Step "8) Free port 8080"
netstat -ano | Select-String ":8080\s" | ForEach-Object {
  $parts = ($_ -split "\s+") | Where-Object { $_ -ne "" }
  $procId = $parts[-1]
  if ($procId -match '^\d+$' -and $procId -ne '0') {
    Write-Host "Stopping PID $procId on :8080"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
}
Start-Sleep -Seconds 1

Write-Step "9) prisma db push + generate"
npx prisma db push
npx prisma generate

Write-Step "10) check-db (must PASS)"
npx tsx scripts/check-db.ts
if ($LASTEXITCODE -ne 0) { throw "check-db failed" }

Write-Step "11) Create practice user"
npx tsx scripts/create-practice-user.ts --email $Email --password $Password
if ($LASTEXITCODE -ne 0) {
  Write-Host "create-practice-user returned non-zero (email may already exist) - trying login path later"
}

Write-Step "12) Sanity: UHRS ping needs API - starting next"
Write-Host ""
Write-Host "LOGIN:"
Write-Host "  URL:      http://localhost:3000/login"
Write-Host "  Email:    $Email"
Write-Host "  Password: $Password"
Write-Host "  Then:     http://localhost:3000/uhrs"
Write-Host ""
Write-Host "Frontend (other window):"
Write-Host '  cd frontend; npm run dev'
Write-Host ""

if ($SkipApiStart) {
  Write-Host "SkipApiStart set - run manually: npx tsx watch src\index.ts"
  exit 0
}

Write-Step "13) Start API (boot must show Database: 127.0.0.1:5433/vetted_db)"
npx tsx watch src\index.ts
