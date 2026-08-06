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

if [[ ! -f "$KEY" ]]; then
  echo "SSH key not found: $KEY" >&2
  exit 1
fi

echo "Uploading virtue files to $REMOTE ..."
scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/main.py" \
  "$ROOT_LOCAL/broker.py" \
  "$ROOT_LOCAL/strategy.py" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/engine/alpaca_spy_feed.py" \
  "$ROOT_LOCAL/engine/databento_mes_feed.py" \
  "$ROOT_LOCAL/engine/webull_clients.py" \
  "$ROOT_LOCAL/engine/webull_openapi.py" \
  "$ROOT_LOCAL/engine/webull_futures.py" \
  "$ROOT_LOCAL/engine/config.py" \
  "$ROOT_LOCAL/engine/dual_sleeve.py" \
  "$ROOT_LOCAL/engine/sleeve_order_router.py" \
  "$ROOT_LOCAL/engine/entry_structure.py" \
  "$ROOT_LOCAL/engine/macromathics_core.py" \
  "$ROOT_LOCAL/engine/observability.py" \
  "$ROOT_LOCAL/engine/futures_broker_adapter.py" \
  "$ROOT_LOCAL/engine/env_loader.py" \
  "$ROOT_LOCAL/engine/ui_state_bridge.py" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/engine/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/scripts/run_daily_session.py" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/scripts/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/requirements.txt" \
  "$REMOTE:/home/ubuntu/FutureMathics.ai/"

scp -i "$KEY" -o StrictHostKeyChecking=no \
  "$ROOT_LOCAL/deploy/systemd/futuremathics_virtue.service" \
  "$REMOTE:/tmp/futuremathics_virtue.service"

ssh -i "$KEY" -o StrictHostKeyChecking=no "$REMOTE" bash -s <<'EOF'
set -euo pipefail
sudo cp /tmp/futuremathics_virtue.service /etc/systemd/system/futuremathics_virtue.service
sudo systemctl daemon-reload
# Ensure Databento client is installed for CME MES L1
python3 -m pip install --user -q 'databento>=0.45.0,<1.0.0' || sudo python3 -m pip install -q 'databento>=0.45.0,<1.0.0' || true
sudo systemctl enable futuremathics_virtue
sudo systemctl restart futuremathics_virtue
sudo systemctl restart futuremathics_dashboard || true
sleep 4
systemctl is-active futuremathics_virtue
systemctl is-active futuremathics_dashboard || true
sudo journalctl -u futuremathics_virtue -n 50 --no-pager
EOF

echo "Done."
