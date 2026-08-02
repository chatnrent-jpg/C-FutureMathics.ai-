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
# Enable CME Globex timetable on the host when Databento key is present (no secret echo).
$cmeEnvPy = @'
from pathlib import Path
p = Path("/home/ubuntu/FutureMathics.ai/.env.local")
text = p.read_text(encoding="utf-8") if p.exists() else ""
lines = text.splitlines()
keys = {}
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        keys[k.strip()] = v
if not keys.get("DATABENTO_API_KEY", "").strip():
    print("WARN: DATABENTO_API_KEY missing in .env.local — still RTH/Alpaca mode")
else:
    keys["FM_DATA_SOURCE"] = "databento"
    keys["VIRTUE_SESSION_MODE"] = "cme"
    out = []
    seen = set()
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in ("FM_DATA_SOURCE", "VIRTUE_SESSION_MODE"):
                out.append(f"{k}={keys[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k in ("FM_DATA_SOURCE", "VIRTUE_SESSION_MODE"):
        if k not in seen:
            out.append(f"{k}={keys[k]}")
    p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    print("CME timetable enabled (FM_DATA_SOURCE=databento VIRTUE_SESSION_MODE=cme)")
'@
$cmeEnvB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cmeEnvPy))

$remoteCmd += @(
    "python3 -m pip install --user -q 'databento>=0.45.0,<1.0.0' || sudo python3 -m pip install -q 'databento>=0.45.0,<1.0.0' || true",
    "python3 -c `"import base64,pathlib; pathlib.Path('/tmp/fm_cme_env.py').write_bytes(base64.b64decode('$cmeEnvB64'))`" && python3 /tmp/fm_cme_env.py",
    "sudo systemctl enable futuremathics_virtue",
    "sudo systemctl restart futuremathics_virtue",
    "sudo systemctl restart futuremathics_dashboard || true",
    "sleep 4",
    "systemctl is-active futuremathics_virtue",
    "systemctl is-active futuremathics_dashboard",
    "sudo journalctl -u futuremathics_virtue -n 50 --no-pager"
)
ssh @ssh $Remote ($remoteCmd -join " && ")

Write-Host "Done."

