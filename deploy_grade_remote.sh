#!/bin/bash
# Install grade-path service on AWS (run ON the server)
set -e
cd /home/ubuntu/FutureMathics.ai

mkdir -p data engine celine scripts deploy/systemd

# Point grade feed at local synced copy
if ! grep -q 'FM_VOLUMEWATCH_STATE_PATH' .env.local 2>/dev/null; then
  echo '' >> .env.local
  echo 'FM_VOLUMEWATCH_STATE_PATH=/home/ubuntu/FutureMathics.ai/data/shared_volumewatch_marketmathics_state.json' >> .env.local
fi

# Install systemd unit
sudo cp deploy/systemd/futuremathics_grade.service /etc/systemd/system/futuremathics_grade.service
sudo systemctl daemon-reload

# Stop old scalp orchestrator if running
sudo systemctl stop futuremathics.service 2>/dev/null || true
sudo systemctl disable futuremathics.service 2>/dev/null || true

# Enable + start grade path
sudo systemctl enable futuremathics_grade.service
sudo systemctl restart futuremathics_grade.service
sleep 3
sudo systemctl status futuremathics_grade.service --no-pager
echo '--- recent logs ---'
sudo journalctl -u futuremathics_grade -n 40 --no-pager
