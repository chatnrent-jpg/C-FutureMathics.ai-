#!/usr/bin/env bash
# Deploy FutureMathics.ai on Ubuntu EC2 (run on server after sync).
set -eu

ROOT="${MARKETMATHICS_ROOT:-/home/ubuntu/FutureMathics.ai}"
PY="${PYTHON_BIN:-/usr/local/bin/python3.14}"

echo "[1/6] Working directory: $ROOT"
cd "$ROOT"

echo "[2/6] Python venv + dependencies"
if [[ ! -x .venv/bin/python ]]; then
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "[3/6] Seed boot state"
.venv/bin/python scripts/bootstrap_system_state.py

echo "[4/6] Install systemd units"
sudo cp deploy/systemd/futuremathics_dashboard.service /etc/systemd/system/
sudo cp deploy/systemd/futuremathics.service /etc/systemd/system/
sudo cp deploy/systemd/futuremathics_tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "[5/6] Enable services"
sudo systemctl enable futuremathics_dashboard.service futuremathics.service futuremathics_tunnel.service
sudo systemctl restart futuremathics_dashboard.service
sleep 3
sudo systemctl restart futuremathics.service
sudo systemctl restart futuremathics_tunnel.service

echo "[6/6] Status"
sudo systemctl is-active futuremathics_dashboard.service futuremathics.service futuremathics_tunnel.service
ss -tlnp | grep 8502 || true
echo "Done. Tunnel URL: sudo journalctl -u futuremathics_tunnel -n 20 --no-pager"
