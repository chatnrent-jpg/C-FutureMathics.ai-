# Deploy / enable FutureMathics virtue service on AWS (Windows PowerShell).
# Usage:
#   cd C:\FutureMathics.ai
#   .\scripts\deploy_virtue_remote.ps1

param(
    [string]$Remote = "ubuntu@54.91.152.140",
    [string]$Key = "C:\MarketMathics.ai\MarketMathics.pem",
    [switch]$StopGrade
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path $Key)) {
    throw "SSH key not found: $Key"
}

$ssh = @("-i", $Key, "-o", "StrictHostKeyChecking=no")

# Ensure AWS .env.local has Databento primary (CME hours). Key travels via scp temp file only.
Write-Host "Syncing Databento env flags on $Remote ..."
$localEnv = Join-Path $Root ".env.local"
$dbKey = ""
if (Test-Path $localEnv) {
    foreach ($line in Get-Content $localEnv) {
        if ($line -match '^\s*DATABENTO_API_KEY=(.+)\s*$') {
            $dbKey = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $dbKey) {
    Write-Warning "DATABENTO_API_KEY missing in local .env.local — AWS may stay on Alpaca/RTH hours"
} else {
    $tmpEnv = Join-Path $env:TEMP ("fm_databento_env_{0}.txt" -f [guid]::NewGuid().ToString("n"))
    try {
        [System.IO.File]::WriteAllLines($tmpEnv, @(
            "DATABENTO_API_KEY=$dbKey"
            "FM_DATA_SOURCE=databento"
        ))
        scp @ssh $tmpEnv "${Remote}:/tmp/fm_databento_env.txt"
        scp @ssh "$Root\scripts\merge_remote_env_keys.py" "${Remote}:/tmp/merge_remote_env_keys.py"
        ssh @ssh $Remote "python3 /tmp/merge_remote_env_keys.py --src /tmp/fm_databento_env.txt --envf /home/ubuntu/FutureMathics.ai/.env.local && rm -f /tmp/merge_remote_env_keys.py /tmp/fm_databento_env.txt"
    } finally {
        Remove-Item -Force $tmpEnv -ErrorAction SilentlyContinue
    }
}

Write-Host "Uploading virtue files to $Remote ..."

scp @ssh `
    "$Root\main.py" `
    "$Root\broker.py" `
    "$Root\strategy.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/"

scp @ssh `
    "$Root\engine\alpaca_spy_feed.py" `
    "$Root\engine\databento_mes_feed.py" `
    "$Root\engine\webull_clients.py" `
    "$Root\engine\webull_openapi.py" `
    "$Root\engine\webull_futures.py" `
    "$Root\engine\config.py" `
    "$Root\engine\futures_broker_adapter.py" `
    "$Root\engine\env_loader.py" `
    "$Root\engine\ui_state_bridge.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/engine/"

scp @ssh `
    "$Root\requirements.txt" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/"

# main.py imports scripts.run_daily_session.in_market_hours + manus risk/heartbeat
scp @ssh `
    "$Root\celine\live_vwap.py" `
    "$Root\celine\live_twap.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/celine/"

scp @ssh `
    "$Root\scripts\run_daily_session.py" `
    "$Root\scripts\sandbox_streamlit.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/scripts/"

scp @ssh `
    "$Root\manus\capital_protection.py" `
    "$Root\manus\heartbeat.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/manus/"

scp @ssh `
    "$Root\deploy\systemd\futuremathics_virtue.service" `
    "${Remote}:/tmp/futuremathics_virtue.service"

Write-Host "Installing and restarting futuremathics_virtue ..."
# Single-line remote command avoids PowerShell CRLF breaking bash `set -o pipefail`
$remoteCmd = @(
    "sudo cp /tmp/futuremathics_virtue.service /etc/systemd/system/futuremathics_virtue.service",
    "sudo systemctl daemon-reload"
)
if ($StopGrade) {
    $remoteCmd += @(
        "sudo systemctl stop futuremathics_grade || true",
        "sudo systemctl disable futuremathics_grade || true"
    )
} else {
    $remoteCmd += "echo Leaving futuremathics_grade as-is use -StopGrade to cut over"
}
$remoteCmd += @(
    "python3 -m pip install --user -q 'databento>=0.45.0,<1.0.0' || sudo python3 -m pip install -q 'databento>=0.45.0,<1.0.0' || true",
    "sudo systemctl enable futuremathics_virtue",
    "sudo systemctl restart futuremathics_virtue",
    "sudo systemctl restart futuremathics_dashboard || true",
    "sleep 4",
    "systemctl is-active futuremathics_virtue",
    "systemctl is-active futuremathics_dashboard",
    "sudo journalctl -u futuremathics_virtue -n 40 --no-pager"
)
ssh @ssh $Remote ($remoteCmd -join " && ")

Write-Host "Done."

