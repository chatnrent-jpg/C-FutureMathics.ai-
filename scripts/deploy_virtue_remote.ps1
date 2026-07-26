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

Write-Host "Uploading virtue files to $Remote ..."

scp @ssh `
    "$Root\main.py" `
    "$Root\broker.py" `
    "$Root\strategy.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/"

scp @ssh `
    "$Root\engine\alpaca_spy_feed.py" `
    "$Root\engine\webull_clients.py" `
    "$Root\engine\webull_openapi.py" `
    "$Root\engine\webull_futures.py" `
    "$Root\engine\config.py" `
    "$Root\engine\futures_broker_adapter.py" `
    "$Root\engine\env_loader.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/engine/"

# main.py imports scripts.run_daily_session.in_market_hours + manus risk/heartbeat
scp @ssh `
    "$Root\scripts\run_daily_session.py" `
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
    "sudo systemctl enable futuremathics_virtue",
    "sudo systemctl restart futuremathics_virtue",
    "sleep 4",
    "systemctl is-active futuremathics_virtue",
    "sudo journalctl -u futuremathics_virtue -n 40 --no-pager"
)
ssh @ssh $Remote ($remoteCmd -join " && ")

Write-Host "Done."

