# Sync VolumeWatch Macro Hub grade (what's on your screen) to AWS every 20s.
# Keep Macro Hub open. This only mirrors the hub JSON to AWS.

$ErrorActionPreference = "Continue"
$remote = "ubuntu@54.91.152.140"
$key = "C:\MarketMathics.ai\MarketMathics.pem"
$src = "C:\Volumewatch\shared_volumewatch_marketmathics_state.json"
$stamp = "C:\FutureMathics.ai\scripts\stamp_vw_grade_for_aws.py"
$tmp = Join-Path $env:TEMP "fm_vw_grade_sync.json"
$dest = "${remote}:/home/ubuntu/FutureMathics.ai/data/shared_volumewatch_marketmathics_state.json"
$log = "C:\FutureMathics.ai\data\vw_grade_sync.log"

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line
}

Write-Log "START Macro Hub -> AWS grade sync (every 20s)"
Write-Log "Source: $src"

while ($true) {
    try {
        if (-not (Test-Path $src)) {
            Write-Log "MISSING hub state - open Volume Watch Macro Hub"
            Start-Sleep -Seconds 20
            continue
        }

        $out = & python $stamp $src $tmp 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Log "stamp failed: $out"
            Start-Sleep -Seconds 20
            continue
        }

        & scp -i $key -o StrictHostKeyChecking=no -o ConnectTimeout=15 $tmp $dest 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "synced hub score $out"
        } else {
            Write-Log "scp failed exit=$LASTEXITCODE"
        }
    } catch {
        Write-Log "error: $_"
    }
    Start-Sleep -Seconds 20
}
