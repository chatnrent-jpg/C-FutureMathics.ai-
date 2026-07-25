#!/bin/bash
echo "Deploying swing trading system..."

cd /home/ubuntu/FutureMathics.ai

# Backup
mkdir -p backups/swing_$(date +%Y%m%d)
cp engine/config.py backups/swing_$(date +%Y%m%d)/ 2>/dev/null || true

# Deploy files
sudo cp /tmp/technical_indicators.py celine/
sudo cp /tmp/swing_signals.py celine/
sudo cp /tmp/run_swing_trading.py scripts/
sudo cp /tmp/config.py engine/
sudo cp /tmp/futures_position_manager.py engine/

# Permissions
sudo chown -R ubuntu:ubuntu celine/ scripts/ engine/
chmod +x scripts/run_swing_trading.py

# Update systemd
sudo sed -i 's|scripts/run_daily_session.py|scripts/run_swing_trading.py|' /etc/systemd/system/futuremathics.service

# Restart
sudo systemctl daemon-reload
sudo systemctl restart futuremathics
sleep 3

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Watching logs (Ctrl+C to exit)..."
sudo journalctl -u futuremathics -f
