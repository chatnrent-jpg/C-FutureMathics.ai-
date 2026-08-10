$ErrorActionPreference = "Stop"
$Repo = Join-Path $HOME "Documents\vettedme-backend"
$Zip  = Join-Path $HOME "Downloads\vettedme-rlhf-changes.zip"
$Branch = "cursor/rlhf-calibration-analytics-8175"

if (-not (Test-Path $Repo)) { throw "Repo not found: $Repo" }
if (-not (Test-Path $Zip))  { throw "Download missing: $Zip  (get vettedme-rlhf-changes.zip from the Cursor agent Artifacts)" }

Set-Location $Repo
git checkout main
git pull origin main
git checkout -B $Branch

Expand-Archive -Path $Zip -DestinationPath $Repo -Force
git add -A
git status
git commit -m "feat(rlhf): Module 1 core rubric, validate engine, calibration analytics"
git push -u origin $Branch
Write-Host "Done. Open a PR from $Branch -> main on GitHub."
