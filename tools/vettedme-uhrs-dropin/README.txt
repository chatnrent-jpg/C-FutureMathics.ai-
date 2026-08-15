VettedME UHRS Windows restore

ONE COMMAND (recommended):
  cd Documents\vettedme-backend
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/chatnrent-jpg/C-FutureMathics.ai-/cursor/uhrs-dropin-sync-8175/tools/vettedme-uhrs-dropin/NUKE-AND-RESTORE.ps1" -OutFile .\NUKE-AND-RESTORE.ps1
  powershell -ExecutionPolicy Bypass -File .\NUKE-AND-RESTORE.ps1

This will:
  - write .env (DB 5433, API 8080)
  - wipe/recreate Docker Postgres on 5433
  - wait until healthy
  - sync UHRS + auth + controller/routes
  - prisma db push + generate
  - create practice user
  - start API

Login: http://localhost:3000/login
  email/password printed by the script (default chatnrent@gmail.com / Practice123!)
