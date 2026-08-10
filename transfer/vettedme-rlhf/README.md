# VettedME RLHF transfer package

Temporary handoff because the cloud agent cannot push to `chatnrent-jpg/vettedme-backend`.

## Files
- `vettedme-rlhf-changes.zip` — overlay onto a fresh `vettedme-backend` clone
- `rlhf-calibration-analytics-8175.bundle` — optional git bundle
- `apply-and-push-rlhf.ps1` — Windows helper

## Windows (PowerShell)

```powershell
cd $HOME\Documents\vettedme-backend
git checkout main
git pull origin main
git checkout -B cursor/rlhf-calibration-analytics-8175

# Download zip from this repo raw URL or litterbox, then:
Expand-Archive -Path "$HOME\Downloads\vettedme-rlhf-changes.zip" -DestinationPath . -Force

git add -A
git commit -m "feat(rlhf): Module 1 core rubric, validate engine, calibration analytics"
git push -u origin cursor/rlhf-calibration-analytics-8175
```
