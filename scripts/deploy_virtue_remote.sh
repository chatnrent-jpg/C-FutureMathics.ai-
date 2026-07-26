#!/usr/bin/env bash
# Deploy / enable FutureMathics virtue service on AWS.
# Usage (from laptop with key):
#   bash scripts/deploy_virtue_remote.sh
set -euo pipefail

REMOTE="${REMOTE:-ubuntu@54.91.152.140}"
KEY="${KEY:-$HOME/MarketMathics.ai/MarketMathics.pem}"
# Windows default if present
if [[ ! -f "$KEY" && -f "/c/MarketMathics.ai/MarketMathics.pem" ]]; then
  KEY="/c/MarketMathics.ai/MarketMathics.pem"
fi
ROOT_LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

echo "Uploading virtue files to $REMOTE ..."
scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/main.py" \
  "$ROOT_LOCAL/broker.py" \
  "$ROOT_LOCAL/strategy.py" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/engine/alpaca_spy_feed.py" \
  "$ROOT_LOCAL/engine/webull_clients.py" \
  "$ROOT_LOCAL/engine/webull_openapi.py" \
  "$ROOT_LOCAL/engine/config.py" \
  "$ROOT_LOCAL/engine/futures_broker_adapter.py" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/engine/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/deploy/systemd/futuremathics_virtue.service" \
  "$REMOTE:/tmp/futuremathics_virtue.service"

ssh -i "$KEY" -o StrictHostKeyChecking=no "$REMOTE" bash -s <<'EOF'
set -euo pipefail
sudo cp /tmp/futuremathics_virtue.service /etc/systemd/system/futuremathics_virtue.service
sudo systemctl daemon-reload
# Keep grade path stopped when promoting virtue (optional — comment out to run both)
# sudo systemctl stop futuremathics_grade || true
# sudo systemctl disable futuremathics_grade || true
sudo systemctl enable futuremathics_virtue
sudo systemctl restart futuremathics_virtue
sleep 3
systemctl is-active futuremathics_virtue
sudo journalctl -u futuremathics_virtue -n 30 --no-pager
EOF

echo "Done."
