# FutureMathics AWS Diagnostic Script
# Run this in PowerShell to diagnose why system isn't trading

Write-Host "🔍 Connecting to AWS and analyzing logs..." -ForegroundColor Cyan
Write-Host ""

$key = "C:\MarketMathics.ai\MarketMathics.pem"
$remote = "ubuntu@54.91.152.140"

# Step 1: Get today's logs and analyze
Write-Host "Step 1: Getting today's logs..." -ForegroundColor Yellow
ssh -i $key $remote "sudo journalctl -u futuremathics --since today > /tmp/today_logs.txt 2>&1"

# Step 2: Count skip reasons
Write-Host ""
Write-Host "Step 2: Analyzing skip reasons..." -ForegroundColor Yellow
Write-Host ""
Write-Host "=== SKIP REASONS (Why no trades) ===" -ForegroundColor Green
ssh -i $key $remote "grep 'SKIP' /tmp/today_logs.txt | grep -o 'reason[^}]*' | cut -d':' -f2 | tr -d ' \",' | sort | uniq -c | sort -rn"

# Step 3: Check system stats
Write-Host ""
Write-Host "=== SYSTEM STATISTICS ===" -ForegroundColor Green
Write-Host "Total cycles today: " -NoNewline
ssh -i $key $remote "grep 'CYCLE START' /tmp/today_logs.txt | wc -l"

Write-Host "Total signals generated: " -NoNewline
ssh -i $key $remote "grep 'CELINE SIGNAL' /tmp/today_logs.txt | wc -l"

Write-Host "Total trades executed: " -NoNewline
ssh -i $key $remote "grep -E 'PAPER_ROUTE|LIVE_ROUTE' /tmp/today_logs.txt | wc -l"

# Step 4: Check if system is halted
Write-Host ""
Write-Host "=== HALT CHECK ===" -ForegroundColor Green
ssh -i $key $remote "grep -i 'HALT' /tmp/today_logs.txt | tail -5"

# Step 5: Check Alpaca status
Write-Host ""
Write-Host "=== ALPACA DATA FEED STATUS ===" -ForegroundColor Green
ssh -i $key $remote "grep -i 'alpaca' /tmp/today_logs.txt | head -10"

# Step 6: Sample VWAP distances
Write-Host ""
Write-Host "=== SAMPLE VWAP DISTANCES (last 10) ===" -ForegroundColor Green
ssh -i $key $remote "grep 'vwap:' /tmp/today_logs.txt | tail -10"

# Step 7: Sample confidence scores
Write-Host ""
Write-Host "=== SAMPLE CONFIDENCE SCORES ===" -ForegroundColor Green
ssh -i $key $remote "grep 'confidence' /tmp/today_logs.txt | tail -10"

Write-Host ""
Write-Host "✅ Diagnostic complete!" -ForegroundColor Cyan
Write-Host ""
Write-Host "👆 Look at the 'SKIP REASONS' section above." -ForegroundColor Yellow
Write-Host "The top reason is why the system isn't trading." -ForegroundColor Yellow
