#!/bin/bash
# Alpaca Integration Deployment Script
# Run on AWS server: bash deploy_alpaca.sh

echo "🚀 FutureMathics Alpaca Deployment"
echo "=================================="

cd /home/ubuntu/FutureMathics

echo ""
echo "Step 1: Backup existing files..."
cp engine/futures_broker_adapter.py engine/futures_broker_adapter.py.backup
cp engine/ui_state_bridge.py engine/ui_state_bridge.py.backup
echo "✅ Backup complete"

echo ""
echo "Step 2: Deploy new files..."
sudo cp /tmp/alpaca_spy_feed.py engine/
sudo cp /tmp/futures_broker_adapter.py engine/
sudo cp /tmp/ui_state_bridge.py engine/
sudo cp /tmp/test_alpaca_spy_feed.py scripts/

echo ""
echo "Step 3: Set permissions..."
sudo chown ubuntu:ubuntu engine/alpaca_spy_feed.py
sudo chown ubuntu:ubuntu scripts/test_alpaca_spy_feed.py

echo ""
echo "Step 4: Verify files..."
ls -lh engine/alpaca_spy_feed.py
ls -lh scripts/test_alpaca_spy_feed.py

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Next steps:"
echo "1. Add Alpaca credentials to .env.local"
echo "2. Test: python3 scripts/test_alpaca_spy_feed.py"
echo "3. Restart: sudo systemctl restart futuremathics"
