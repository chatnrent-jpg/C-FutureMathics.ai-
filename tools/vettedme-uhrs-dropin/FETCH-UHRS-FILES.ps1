# FETCH-UHRS-FILES.ps1 — pull each UHRS file from GitHub into THIS folder.
# Run from: C:\Users\Henry Okojie\Documents\vettedme-backend
#
#   powershell -ExecutionPolicy Bypass -File .\FETCH-UHRS-FILES.ps1
#   npx prisma generate
#   npx prisma db push
#   npx tsx watch src\index.ts
#
# Boot log MUST show: "UHRS simulator MOUNTED"
# Then: curl.exe http://localhost:8080/api/rlhf/uhrs/ping  → {"uhrs":true}

$ErrorActionPreference = "Stop"
$Dest = (Get-Location).Path
if (-not (Test-Path "$Dest\src\index.ts")) {
  throw "Run this from vettedme-backend (src\index.ts not found). Current: $Dest"
}

$Base = "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin"
$Files = @(
  "src/modules/rlhf-core-rubric/uhrsService.ts",
  "src/modules/rlhf-core-rubric/uhrsController.ts",
  "src/modules/rlhf-core-rubric/uhrsRoutes.ts",
  "src/modules/rlhf-core-rubric/routes.ts",
  "src/modules/rlhf-core-rubric/validation.ts",
  "src/index.ts",
  "prisma/schema.prisma",
  "frontend/src/lib/uhrsApi.ts",
  "frontend/src/app/uhrs/page.tsx"
)

Write-Host "=== Fetch UHRS files into $Dest ==="
foreach ($rel in $Files) {
  $url = "$Base/$rel"
  $out = Join-Path $Dest ($rel -replace "/", "\")
  $dir = Split-Path $out -Parent
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  Write-Host "GET $rel"
  Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
  if ((Get-Item $out).Length -lt 50) { throw "Download too small: $out" }
}

# Prove critical markers landed
$idx = Get-Content "$Dest\src\index.ts" -Raw
if ($idx -notmatch "uhrsRoutes") { throw "src\index.ts missing uhrsRoutes import — fetch failed" }
if ($idx -notmatch "UHRS simulator MOUNTED") { throw "src\index.ts missing UHRS boot log" }
if (-not (Test-Path "$Dest\src\modules\rlhf-core-rubric\uhrsRoutes.ts")) {
  throw "uhrsRoutes.ts missing"
}
Write-Host "OK: index.ts imports uhrsRoutes + boot banner present"

# Free port 8080
Write-Host "Killing port 8080..."
netstat -ano | Select-String ":8080\s" | ForEach-Object {
  $parts = ($_ -split "\s+") | Where-Object { $_ -ne "" }
  $procId = $parts[-1]
  if ($procId -match '^\d+$' -and $procId -ne '0') {
    Write-Host "Stop-Process $procId"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
}

Write-Host ""
Write-Host "Next:"
Write-Host "  npx prisma generate"
Write-Host "  npx prisma db push"
Write-Host "  npx tsx watch src\index.ts"
Write-Host "Look for log line: UHRS simulator MOUNTED"
Write-Host "Then: curl.exe http://localhost:8080/api/rlhf/uhrs/ping"
