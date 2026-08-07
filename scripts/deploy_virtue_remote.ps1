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

# Paper/default: Alpaca SPY->MES + cash RTH. Keep Databento key on box as standby only.
# Flip back later with FM_DATA_SOURCE=databento + VIRTUE_SESSION_MODE=cme if needed.
Write-Host "Syncing Alpaca/RTH primary (+ Databento standby key) on $Remote ..."
$localEnv = Join-Path $Root ".env.local"
$dbKey = ""
if (Test-Path $localEnv) {
    foreach ($line in Get-Content $localEnv) {
        if ($line -match '^\s*DATABENTO_API_KEY=(.+)\s*$') {
            $dbKey = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}
$tmpEnv = Join-Path $env:TEMP ("fm_market_data_env_{0}.txt" -f [guid]::NewGuid().ToString("n"))
try {
    $envLines = @(
        "FM_DATA_SOURCE=alpaca",
        "VIRTUE_SESSION_MODE=rth",
        # Temperance simplify: tactical-only 1 MES (override any stale 2-MES remote env)
        "FM_VIRTUE_CORE_ENABLED=0",
        "FM_MAX_ACCOUNT_CONTRACT_CEILING=1",
        "FM_PAPER_MAX_MES_CONTRACTS=1",
        # MacroMathics: bands + stop/TP/lock only (kill overlapping indicator gates)
        "FM_VIRTUE_SIMPLE_STACK=1"
    )
    if ($dbKey) {
        $envLines += "DATABENTO_API_KEY=$dbKey"
    } else {
        Write-Warning "DATABENTO_API_KEY missing locally - standby key not synced (Alpaca/RTH still applied)"
    }
    [System.IO.File]::WriteAllLines($tmpEnv, $envLines)
    scp @ssh $tmpEnv "${Remote}:/tmp/fm_market_data_env.txt"
    scp @ssh "$Root\scripts\merge_remote_env_keys.py" "${Remote}:/tmp/merge_remote_env_keys.py"
    $mergeCmd = "python3 /tmp/merge_remote_env_keys.py --src /tmp/fm_market_data_env.txt --envf /home/ubuntu/FutureMathics.ai/.env.local; rm -f /tmp/merge_remote_env_keys.py /tmp/fm_market_data_env.txt"
    ssh @ssh $Remote $mergeCmd
} finally {
    Remove-Item -Force $tmpEnv -ErrorAction SilentlyContinue
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
    "$Root\engine\dual_sleeve.py" `
    "$Root\engine\sleeve_order_router.py" `
    "$Root\engine\entry_structure.py" `
    "$Root\engine\macromathics_core.py" `
    "$Root\engine\observability.py" `
    "$Root\engine\futures_broker_adapter.py" `
    "$Root\engine\env_loader.py" `
    "$Root\engine\ui_state_bridge.py" `
    "$Root\engine\regime_engine.py" `
    "$Root\engine\entry_quality.py" `
    "$Root\engine\institutional_sizing.py" `
    "$Root\engine\institutional_exits.py" `
    "$Root\engine\institutional_monitor.py" `
    "$Root\engine\trade_history.py" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/engine/"

scp @ssh `
    "$Root\requirements.txt" `
    "${Remote}:/home/ubuntu/FutureMathics.ai/"

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
$parts = @(
    "sudo cp /tmp/futuremathics_virtue.service /etc/systemd/system/futuremathics_virtue.service",
    "sudo systemctl daemon-reload"
)
if ($StopGrade) {
    $parts += @(
        "sudo systemctl stop futuremathics_grade || true",
        "sudo systemctl disable futuremathics_grade || true"
    )
} else {
    $parts += "echo Leaving futuremathics_grade as-is use -StopGrade to cut over"
}
$parts += @(
    "python3 -m pip install --user --break-system-packages -q 'databento>=0.45.0,<1.0.0' || true",
    "sudo systemctl enable futuremathics_virtue",
    "sudo systemctl restart futuremathics_virtue",
    "sudo systemctl restart futuremathics_dashboard || true",
    "sleep 4",
    "systemctl is-active futuremathics_virtue",
    "systemctl is-active futuremathics_dashboard",
    "sudo journalctl -u futuremathics_virtue -n 50 --no-pager"
)
$remoteCmd = $parts -join " && "
ssh @ssh $Remote $remoteCmd

Write-Host "Done."
