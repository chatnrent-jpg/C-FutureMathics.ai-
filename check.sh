#!/bin/bash
# Quick diagnostic script

echo "Getting logs..."
sudo journalctl -u futuremathics --since today > ~/today_logs.txt

echo ""
echo "=== SKIP REASONS ==="
grep "SKIP" ~/today_logs.txt | grep -o 'reason[^}]*' | cut -d':' -f2 | tr -d ' ",' | sort | uniq -c | sort -rn

echo ""
echo "=== STATS ==="
echo "Cycles: $(grep 'CYCLE START' ~/today_logs.txt | wc -l)"
echo "Trades: $(grep -E 'PAPER_ROUTE|LIVE_ROUTE' ~/today_logs.txt | wc -l)"

echo ""
echo "=== HALTED? ==="
grep -i "HALT" ~/today_logs.txt | tail -3

echo ""
echo "=== LAST 5 SIGNALS ==="
grep "regime:" ~/today_logs.txt | tail -5
