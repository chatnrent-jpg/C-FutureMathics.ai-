# FutureMathics — MES paper session (PowerShell)
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
$env:PYTHONUNBUFFERED = "1"
$env:FM_IGNORE_MARKET_HOURS = "1"
Write-Host "Starting FutureMathics MES orchestrator (paper, 24/7 cycles)..."
python scripts\run_daily_session.py
