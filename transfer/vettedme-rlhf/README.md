# VettedME RLHF transfer package

Temporary handoff because the cloud agent cannot push to `chatnrent-jpg/vettedme-backend`.

## Latest — Supervisor Admin Analytics

- `vettedme-supervisor-analytics.zip` — overlay `controller.ts` + `routes.ts` (+ note)
- Endpoint: `GET /api/v1/modules/rlhf-core-rubric/admin/analytics` (ADMIN Bearer)

```powershell
cd $HOME\Documents\vettedme-backend
git checkout cursor/rlhf-calibration-analytics-8175
Expand-Archive -Path "$HOME\Downloads\vettedme-supervisor-analytics.zip" -DestinationPath . -Force
git add src/modules/rlhf-core-rubric/controller.ts src/modules/rlhf-core-rubric/routes.ts
git commit -m "feat(rlhf): add Supervisor Admin analytics data table API"
git push origin cursor/rlhf-calibration-analytics-8175
```

## Earlier full overlay

- `vettedme-rlhf-changes.zip` — broader Module 1 overlay
- `rlhf-calibration-analytics-8175.bundle` — optional git bundle
- `apply-and-push-rlhf.ps1` — Windows helper for the full overlay
