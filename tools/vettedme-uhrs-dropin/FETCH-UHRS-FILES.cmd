@echo off
setlocal EnableExtensions
cd /d "%~dp0"
REM If this .cmd lives in FutureMathics tools folder, user should copy it.
REM Preferred: run from vettedme-backend after downloading this file there.

if not exist "src\index.ts" (
  echo ERROR: Run from vettedme-backend folder ^(src\index.ts missing^).
  exit /b 1
)

set BASE=https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin

call :get src/modules/rlhf-core-rubric/uhrsService.ts
call :get src/modules/rlhf-core-rubric/uhrsController.ts
call :get src/modules/rlhf-core-rubric/uhrsRoutes.ts
call :get src/modules/rlhf-core-rubric/routes.ts
call :get src/modules/rlhf-core-rubric/validation.ts
call :get src/index.ts
call :get prisma/schema.prisma
call :get frontend/src/lib/uhrsApi.ts
call :get frontend/src/app/uhrs/page.tsx

findstr /C:"uhrsRoutes" src\index.ts >nul || (echo ERROR: index.ts missing uhrsRoutes & exit /b 1)
findstr /C:"UHRS simulator MOUNTED" src\index.ts >nul || (echo ERROR: index.ts missing UHRS boot log & exit /b 1)
if not exist src\modules\rlhf-core-rubric\uhrsRoutes.ts (echo ERROR: uhrsRoutes.ts missing & exit /b 1)

echo OK: UHRS files present
echo Next: npx prisma generate ^&^& npx prisma db push ^&^& npx tsx watch src\index.ts
exit /b 0

:get
set REL=%~1
set OUT=%REL:/=\%
for %%I in ("%OUT%") do if not exist "%%~dpI" mkdir "%%~dpI"
echo GET %REL%
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%BASE%/%REL%' -OutFile '%OUT%' -UseBasicParsing"
if not exist "%OUT%" (echo FAILED %REL% & exit /b 1)
exit /b 0
