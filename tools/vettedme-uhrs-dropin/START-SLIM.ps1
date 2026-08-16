# START-SLIM.ps1 - boot auth+UHRS only (skips crashing RLHF router)
# Run from vettedme-backend:
#   powershell -ExecutionPolicy Bypass -File .\START-SLIM.ps1

$ErrorActionPreference = "Stop"
if (-not (Test-Path ".\src\index.ts")) {
  throw "cd into Documents\vettedme-backend first"
}

$Base = "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin"
$Files = @(
  "src/slim-api.ts",
  "src/env.ts",
  "src/routes/auth.routes.ts",
  "src/middleware/errorHandler.ts",
  "src/middleware/auth.ts",
  "src/modules/rlhf-core-rubric/uhrsRoutes.ts",
  "src/modules/rlhf-core-rubric/uhrsController.ts",
  "src/modules/rlhf-core-rubric/uhrsService.ts",
  "src/modules/rlhf-core-rubric/validation.ts"
)

Write-Host "Downloading slim-api files..."
foreach ($rel in $Files) {
  $out = Join-Path (Get-Location) ($rel -replace "/", "\")
  New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
  Write-Host "GET $rel"
  Invoke-WebRequest -Uri "$Base/$rel" -OutFile $out -UseBasicParsing
}

Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

Write-Host "Freeing port 8080..."
cmd /c "for /f ""tokens=5"" %a in ('netstat -ano ^| findstr :8080') do taskkill /F /PID %a >nul 2>&1"

Write-Host ""
Write-Host "Starting slim-api on :8080 (leave this window open)"
Write-Host "Then open http://localhost:3000/login"
Write-Host "Login: chatnrent@gmail.com / Practice123!"
Write-Host ""
npx tsx watch src\slim-api.ts
