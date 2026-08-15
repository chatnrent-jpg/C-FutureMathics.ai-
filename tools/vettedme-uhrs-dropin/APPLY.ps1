# Apply UHRS drop-in into vettedme-backend (run from this folder OR pass -Dest)
param(
  [string]$Dest = "C:\Users\Henry Okojie\Documents\vettedme-backend"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "Copying UHRS files from $Here -> $Dest"
Copy-Item -Force "$Here\src\modules\rlhf-core-rubric\*" "$Dest\src\modules\rlhf-core-rubric\"
Copy-Item -Force "$Here\prisma\schema.prisma" "$Dest\prisma\schema.prisma"
New-Item -ItemType Directory -Force -Path "$Dest\frontend\src\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$Dest\frontend\src\app\uhrs" | Out-Null
New-Item -ItemType Directory -Force -Path "$Dest\frontend\src\app\talent" | Out-Null
New-Item -ItemType Directory -Force -Path "$Dest\tests\unit" | Out-Null
Copy-Item -Force "$Here\frontend\src\lib\uhrsApi.ts" "$Dest\frontend\src\lib\uhrsApi.ts"
Copy-Item -Force "$Here\frontend\src\app\uhrs\page.tsx" "$Dest\frontend\src\app\uhrs\page.tsx"
Copy-Item -Force "$Here\frontend\src\app\talent\layout.tsx" "$Dest\frontend\src\app\talent\layout.tsx"
Copy-Item -Force "$Here\tests\unit\uhrs-scoring.test.ts" "$Dest\tests\unit\uhrs-scoring.test.ts"
Write-Host "OK. Now in $Dest run:"
Write-Host "  npx prisma generate"
Write-Host "  npx prisma db push"
Write-Host "  npx tsx watch src/index.ts"
Write-Host "Expect: curl http://localhost:8080/api/rlhf/uhrs/status -> 401"
