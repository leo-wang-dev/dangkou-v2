#!/usr/bin/env bash
set -euo pipefail
# NEW reproducible trial template. Rendering never installs/enables host services.
BASE=${DANGKOU_TRIAL_BASE:-/home/ubuntu/dangkou-wechat-test}
ENGINE=${DANGKOU_TRIAL_ENGINE:-/home/ubuntu/dsh-wechat-test}
mode=${1:---render}
if [[ "$mode" != --render && "$mode" != --install ]]; then
  echo 'Usage: wechat-test-services.sh --render OUTPUT_DIR | --install' >&2; exit 2
fi
out=${2:-$(mktemp -d)}
mkdir -p "$out"
for item in api notifications engine user-app index; do
  dependencies='network-online.target'
  case "$item" in
    api) dir="$BASE"; command="$BASE/.venv/bin/python -m uvicorn catalog.main:app --host 127.0.0.1 --port 19010 --no-access-log"; dependencies+=' dangkou-wechat-test-litellm.service' ;;
    notifications) dir="$BASE"; command="$BASE/.venv/bin/python -u scripts/run_notifications.py"; dependencies+=' dangkou-wechat-test-engine.service dangkou-wechat-test-litellm.service' ;;
    engine) dir="$ENGINE"; command="/usr/bin/node host.mjs" ;;
    user-app) dir="$BASE"; command="$BASE/.venv/bin/python -u scripts/run_user_app.py" ;;
    index) dir="$BASE"; command="$BASE/.venv/bin/python -u scripts/rebuild_search_index.py" ;;
  esac
  cat > "$out/dangkou-wechat-test-$item.service" <<UNIT
[Unit]
Description=Isolated Dangkou trial $item
Wants=$dependencies
After=$dependencies
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
done
cat > "$out/dangkou-wechat-test-guest-sweep.service" <<UNIT
[Unit]
Description=Expire abandoned trial guest sessions in both databases
After=dangkou-wechat-test-api.service dangkou-wechat-test-user-app.service
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=$BASE
EnvironmentFile=$BASE/.env
ExecStart=$BASE/.venv/bin/python -m catalog.guest_sessions --kind central --db \${USER_APP_DB} --photo-dir \${USER_APP_PHOTOS}
ExecStart=$BASE/.venv/bin/python -m catalog.guest_sessions --kind shop --db \${CATALOG_V2_DB} --photo-dir \${CATALOG_CS_PHOTOS}
UMask=0077
UNIT
cat > "$out/dangkou-wechat-test-guest-sweep.timer" <<'UNIT'
[Unit]
Description=Periodic trial guest cleanup
[Timer]
OnBootSec=15min
OnUnitActiveSec=15min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sed "s|/home/ubuntu/dangkou-wechat-test|$BASE|g" "$(dirname "$0")/dangkou-wechat-test-litellm.service" > "$out/dangkou-wechat-test-litellm.service"
if [[ "$mode" == --install ]]; then
  for unit in "$out"/*.{service,timer}; do sudo install -m 644 "$unit" "/etc/systemd/system/$(basename "$unit")"; done
  sudo systemctl daemon-reload
  sudo systemctl enable --now dangkou-wechat-test-litellm dangkou-wechat-test-api dangkou-wechat-test-notifications dangkou-wechat-test-engine dangkou-wechat-test-user-app dangkou-wechat-test-index dangkou-wechat-test-guest-sweep.timer
else
  echo "Rendered templates in $out; no host services changed."
fi
