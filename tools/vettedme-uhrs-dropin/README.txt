UHRS drop-in for Windows (VettedME)

Your boot log MUST include this line after restart:
  UHRS simulator MOUNTED — GET /api/rlhf/uhrs/ping (expect 200)

If that line is missing, index.ts was NOT updated — UHRS is not loaded.

STEP 1 — from Documents\vettedme-backend:
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin/FETCH-UHRS-FILES.ps1" -OutFile .\FETCH-UHRS-FILES.ps1
  powershell -ExecutionPolicy Bypass -File .\FETCH-UHRS-FILES.ps1
  npx prisma generate
  npx prisma db push
  npx tsx watch src\index.ts

STEP 2 — verify (second window):
  curl.exe http://localhost:8080/api/rlhf/uhrs/ping
  → must return {"uhrs":true,...}
  curl.exe -i http://localhost:8080/api/rlhf/uhrs/status
  → must be 401 (not 404)

Then refresh http://localhost:3000/uhrs
