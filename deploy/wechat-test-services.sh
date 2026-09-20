#!/usr/bin/env bash
set -euo pipefail
# Isolated acceptance instance; does not replace the original merchant services.
BASE=/home/ubuntu/dangkou-wechat-test
ENGINE=/home/ubuntu/dsh-wechat-test
for item in api customer notifications engine; do
  case "$item" in
    api) dir="$BASE"; command="$BASE/.venv/bin/python -m uvicorn catalog.main:app --host 127.0.0.1 --port 19010 --no-access-log" ;;
    customer) dir="$BASE"; command="$BASE/.venv/bin/python -u scripts/run_wechat_customer.py" ;;
    notifications) dir="$BASE"; command="$BASE/.venv/bin/python -u scripts/run_notifications.py" ;;
    engine) dir="$ENGINE"; command="/usr/bin/node host.mjs" ;;
  esac
  unit="dangkou-wechat-test-$item.service"
  cat > "/tmp/$unit" <<UNIT
[Unit]
Description=Isolated Dangkou WeChat acceptance $item
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=$dir
EnvironmentFile=$dir/.env
ExecStart=$command
Restart=on-failure
RestartSec=5
UMask=0077
KillMode=control-group
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
UNIT
  sudo install -m 644 "/tmp/$unit" "/etc/systemd/system/$unit"
done
sudo systemctl daemon-reload
sudo systemctl enable --now dangkou-wechat-test-api dangkou-wechat-test-customer dangkou-wechat-test-notifications dangkou-wechat-test-engine
