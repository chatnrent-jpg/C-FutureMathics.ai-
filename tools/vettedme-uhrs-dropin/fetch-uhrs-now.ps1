$ErrorActionPreference = "Stop"
if (-not (Test-Path ".\src\index.ts")) { throw "Not in vettedme-backend" }
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
  "frontend/src/app/uhrs/page.tsx",
  "scripts/check-db.ts"
)
foreach ($rel in $Files) {
  $out = Join-Path (Get-Location) ($rel -replace "/", "\")
  New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
  Write-Host "GET $rel"
  Invoke-WebRequest -Uri "$Base/$rel" -OutFile $out -UseBasicParsing
}
Select-String -Path ".\src\index.ts" -Pattern "uhrsRoutes" | Out-Host
Select-String -Path ".\src\index.ts" -Pattern "UHRS simulator MOUNTED" | Out-Host
Test-Path ".\src\modules\rlhf-core-rubric\uhrsRoutes.ts"
Write-Host "DONE - run: npx prisma generate; npx prisma db push; npx tsx watch src\index.ts"
