#!/usr/bin/env bash
# Copy code without deleting data, .env, or the merchant's quote template.
# Usage: DEPLOY_HOST=user@server bash deploy/deploy.sh
set -euo pipefail
: "${DEPLOY_HOST:?Set DEPLOY_HOST=user@server (SSH key authentication)}"
REPO_LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
ssh "$DEPLOY_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
mkdir -p "$HOME/dangkou-v2" "$HOME/dangkou-backups"
cd "$HOME/dangkou-v2"

REMOTE
rsync -az --exclude='.env' --exclude='data/' --exclude='.venv/' \
  --exclude='.git/' --exclude='audit/' --exclude='runs/' --exclude='__pycache__/' \
  --exclude='.pytest_cache/' "$REPO_LOCAL/" "$DEPLOY_HOST:dangkou-v2/"
ssh "$DEPLOY_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd "$HOME/dangkou-v2"
test -f .env || { echo 'Missing server .env; copy .env.example and configure it.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/preflight.py
umask 077
# Stop writers before creating a consistent archive of existing data and WAL.
for service in dangkou2 dangkou-cs dangkou-notify dangkou-index; do
  if systemctl is-active --quiet "$service"; then sudo systemctl stop "$service"; fi
done
if [ -d data ]; then
  tar -czf "$HOME/dangkou-backups/data-$(date +%Y%m%d-%H%M%S).tgz" data
fi
for pair in 'dangkou2:sidecar' 'dangkou-cs:bot' 'dangkou-notify:notifications' 'dangkou-index:index'; do
  name=${pair%:*}; role=${pair#*:}
  if [ "$role" = sidecar ]; then
    command="$HOME/dangkou-v2/.venv/bin/python -m uvicorn catalog.main:app --host 127.0.0.1 --port 8890"
  elif [ "$role" = index ]; then
    command="$HOME/dangkou-v2/.venv/bin/python scripts/rebuild_search_index.py"
  elif [ "$role" = notifications ]; then
    command="$HOME/dangkou-v2/.venv/bin/python scripts/run_notifications.py"
  else
    command="$HOME/dangkou-v2/.venv/bin/python scripts/run_cs_bot.py"
  fi
  sudo tee "/etc/systemd/system/$name.service" >/dev/null <<EOF
[Unit]
Description=Dangkou $role
After=network-online.target
[Service]
User=$(id -un)
WorkingDirectory=$HOME/dangkou-v2
EnvironmentFile=$HOME/dangkou-v2/.env
Environment=CATALOG_NOTIFY_WORKER=1
ExecStart=$command
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now dangkou2 dangkou-cs dangkou-notify dangkou-index
curl --fail --silent http://127.0.0.1:8890/health
printf '\nCode deployed. Configure HTTPS reverse proxy and register engine-plugin/catalog-v2.mjs using the installed engine configuration.\n'
REMOTE
