UHRS drop-in for Windows (VettedME)

PREFERRED (no zip / litterbox):
  cd Documents\vettedme-backend
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin/APPLY-UHRS-EMBEDDED.ps1" -OutFile .\APPLY-UHRS-EMBEDDED.ps1
  powershell -ExecutionPolicy Bypass -File .\APPLY-UHRS-EMBEDDED.ps1
  npx tsx watch src/index.ts

VERIFY (must be 401, never 404):
  curl.exe -i http://localhost:8080/api/rlhf/uhrs/status

If still 404: you are not running API from the folder where files were written.
