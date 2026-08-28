#!/usr/bin/env bash
# deploy/deploy.sh —— 清场旧系统 + 部署 dangkou-v2 到服务器 + engine 挂插件
# 用法：bash deploy/deploy.sh   （需本地 sshpass + 服务器密码见变量）
set -euo pipefail

SRV=ubuntu@134.175.135.102
PW='REDACTED'
SSHCMD="sshpass -p $PW ssh -o StrictHostKeyChecking=no $SRV"
SCPCMD="sshpass -p $PW scp -o StrictHostKeyChecking=no"
REPO_LOCAL="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1. 服务器清场（旧 dangkou 全停全删；engine 保留）=="
$SSHCMD 'sudo systemctl stop dangkou 2>/dev/null || true
sudo systemctl disable dangkou 2>/dev/null || true
sudo rm -f /etc/systemd/system/dangkou.service
sudo systemctl daemon-reload
rm -rf ~/dangkou ~/dangkou-v2
# 旧插件退役：摘 catalog.mjs（v1）
rm -f ~/dsh-engine/plugins/catalog.mjs ~/dsh-engine/plugins/catalog-v2.mjs'

echo "== 2. 同步代码 + venv =="
$SCPCMD -r "$REPO_LOCAL/catalog" "$REPO_LOCAL/static" "$REPO_LOCAL/harness" \
    "$REPO_LOCAL/engine-plugin" "$REPO_LOCAL/requirements.txt" "$SRV:~/dangkou-v2-tmp/" 2>/dev/null || \
  $SSHCMD 'mkdir -p ~/dangkou-v2-tmp'
$SCPCMD -r "$REPO_LOCAL/catalog" "$SRV:~/dangkou-v2-tmp/" >/dev/null
$SCPCMD -r "$REPO_LOCAL/static" "$SRV:~/dangkou-v2-tmp/" >/dev/null
$SCPCMD -r "$REPO_LOCAL/harness" "$SRV:~/dangkou-v2-tmp/" >/dev/null
$SCPCMD -r "$REPO_LOCAL/engine-plugin" "$SRV:~/dangkou-v2-tmp/" >/dev/null
$SCPCMD "$REPO_LOCAL/requirements.txt" "$SRV:~/dangkou-v2-tmp/" >/dev/null
$SSHCMD 'mv ~/dangkou-v2-tmp ~/dangkou-v2 && cd ~/dangkou-v2 && python3 -m venv .venv && \
.venv/bin/pip -q install fastapi uvicorn openpyxl requests python-multipart pillow'

echo "== 3. .env（真 key 本地传，不入 git）=="
$SCPCMD "$REPO_LOCAL/.env" "$SRV:~/dangkou-v2/.env" && echo ".env 已传" || \
  echo '!! 本地无 .env：手动在服务器 ~/dangkou-v2/.env 补 CATALOG_AGENT_API_KEY/BAILIAN_API_KEY/CATALOG_V2_SERVICE_TOKEN'

echo "== 4. systemd（User=ubuntu 教训；EnvironmentFile=.env）=="
$SSHCMD 'sudo tee /etc/systemd/system/dangkou2.service > /dev/null << EOF
[Unit]
Description=dangkou catalog v2
After=network.target
[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/dangkou-v2
EnvironmentFile=/home/ubuntu/dangkou-v2/.env
ExecStart=/home/ubuntu/dangkou-v2/.venv/bin/python -m uvicorn catalog.main:app --host 0.0.0.0 --port 8890
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now dangkou2'

echo "== 5. engine 挂插件 + 重启（微信登录态不丢）=="
# host.mjs 用本地改好的版本整文件同步（sed 远程改是脆弱点，弃用）
$SCPCMD "$REPO_LOCAL/engine-plugin/catalog-v2.mjs" "$SRV:~/dsh-engine/plugins/catalog-v2.mjs" >/dev/null
$SCPCMD /Users/elias/code/eryuan/dsh-engine/host.mjs "$SRV:~/dsh-engine/host.mjs" >/dev/null
$SSHCMD 'grep -q "catalog-v2" ~/dsh-engine/host.mjs && echo "host.mjs 挂载点✓" || echo "!! 挂载缺失"
pm2 restart dangkou-engine'

echo "== 6. 冒烟 =="
$SSHCMD 'sleep 3 && curl -s http://127.0.0.1:8890/health && \
curl -s -o /dev/null -w " H5:%{http_code}\n" http://127.0.0.1:8890/ && \
systemctl is-active dangkou2 && pm2 list | grep dangkou-engine'
echo '完成。微信实测：发 Excel → 答品类 → 等回调 → 批工单 → 发图找货 → 发报价单'
