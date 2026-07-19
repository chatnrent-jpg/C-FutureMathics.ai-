# Fetch trade history from AWS and import to local database
# Usage: .\scripts\fetch_aws_history.ps1 ubuntu@your-aws-ip

param(
    [Parameter(Mandatory=$true)]
    [string]$SshTarget,
    
    [int]$DaysBack = 30
)

$ErrorActionPreference = "Stop"

Write-Host "=== FutureMathics AWS Trade History Importer ===" -ForegroundColor Cyan
Write-Host ""

$TempLog = "data\aws_logs_temp.txt"
$Since = (Get-Date).AddDays(-$DaysBack).ToString("yyyy-MM-dd")

Write-Host "[1/3] Fetching logs from AWS (since $Since)..." -ForegroundColor Yellow
ssh $SshTarget "sudo journalctl -u futuremathics.service --since '$Since' --no-pager | grep position_exit" > $TempLog

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to fetch logs from AWS" -ForegroundColor Red
    exit 1
}

$LineCount = (Get-Content $TempLog | Measure-Object -Line).Lines
Write-Host "  Downloaded $LineCount log lines" -ForegroundColor Green
Write-Host ""

if ($LineCount -eq 0) {
    Write-Host "Warning: No position_exit logs found. Is the orchestrator running?" -ForegroundColor Yellow
    exit 0
}

Write-Host "[2/3] Parsing and importing trades to database..." -ForegroundColor Yellow
python scripts\parse_aws_logs.py $TempLog

Write-Host ""
Write-Host "[3/3] Computing daily summaries..." -ForegroundColor Yellow
python scripts\analyze_performance.py --compute-summaries

Write-Host ""
Write-Host "=== Import Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "View performance: python scripts\analyze_performance.py" -ForegroundColor Cyan
Write-Host "Database location: data\trade_history.db" -ForegroundColor Cyan
