#!/usr/bin/env bash
# 演示晨检（2026-10-10 用）——一条命令确认全链路就绪
# 用法: bash ~/dangkou-v2/scripts/demo-morning-check.sh
set -u
C_G="\033[32m"; C_R="\033[31m"; C_Y="\033[33m"; C_0="\033[0m"
ok(){ echo -e "  ${C_G}✅$C_0 $1"; }
bad(){ echo -e "  ${C_R}❌$C_0 $1"; }
warn(){ echo -e "  ${C_Y}⚠️$C_0 $1"; }

echo "== 1. 服务进程 =="
for s in dangkou2 dangkou-notify dangkou-index dangkou-userapp dsh-engine dangkou-litellm; do
  st=$(systemctl is-active $s 2>/dev/null)
  [ "$st" = "active" ] && ok "$s" || bad "$s ($st)"
done

echo "== 2. 端口 =="
for p in 8890 19010 17605 17606 4000; do
  ss -tln | grep -q "127.0.0.1:$p " && ok "port $p" || bad "port $p"
done
curl -sk -o /dev/null -w "" https://106.55.20.61/health && ok "https 443 → health" || bad "https 443"

echo "== 3. FastAPI =="
curl -s http://127.0.0.1:8890/health | grep -q ready && ok "sidecar ready" || bad "sidecar"

echo "== 4. litellm 桥（glm-5.3 → dashscope） =="
R=$(curl -s -m 30 http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_URL /dev/null 2>/dev/null; echo sk-dk-bridge-9f0095cd57b707d62ab11cee07a12c3c)" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3","messages":[{"role":"user","content":"说ok"}],"max_tokens":8}')
echo "$R" | grep -q '"content"' && ok "glm-5.3 经桥可答" || { bad "litellm 无应答"; echo "    $R" | head -c 200; echo; }

echo "== 5. BAILIAN（cs 对话/视觉） =="
BK=$(grep "^BAILIAN_API_KEY=" ~/dangkou-v2/.env | cut -d= -f2-)
R=$(curl -s -m 30 "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" \
  -H "Authorization: Bearer $BK" -H "Content-Type: application/json" \
  -d '{"model":"qwen3.8-max","messages":[{"role":"user","content":"说ok"}],"max_tokens":8}')
echo "$R" | grep -q '"content"' && ok "qwen3.8-max 可答" || { bad "BAILIAN 异常"; echo "    $R" | head -c 200; echo; }

echo "== 6. 微信引擎 =="
AT=$(cat ~/dsh-engine/data/wechat/.admin-token 2>/dev/null)
[ -n "$AT" ] || { bad "admin token 文件缺失"; exit 1; }
ACC=$(curl -s http://127.0.0.1:17605/v1/accounts -H "Authorization: Bearer $AT")
echo "$ACC" | python3 -c "
import json,sys
d=json.load(sys.stdin)
accts=d.get('accounts') or []
if accts:
    print(f'  ✅ 已绑定微信账号: {accts[0].get(\"accountId\",\"?\")}')
else:
    print('  ⚠️ 尚未绑定微信——去管理页「绑定微信」扫码')
"
SESS=$(curl -s http://127.0.0.1:17605/v1/login/session -H "Authorization: Bearer $AT" | python3 -c "import json,sys; s=json.load(sys.stdin).get('session') or {}; print(s.get('state') or 'none')")
echo "  登录会话状态: $SESS"

echo "== 7. 解析容器镜像 =="
sudo docker images 2>/dev/null | grep -q dangkou-parser && ok "dangkou-parser 镜像在" || bad "parser 镜像缺失"
sudo docker ps -a --format "{{.Names}}" | grep -q . && warn "有残留容器（正常情况跑完即删）"

echo "== 8. 数据库基线 =="
cd ~/dangkou-v2 && python3 -c "
import sqlite3
con=sqlite3.connect('data/catalog.db')
cats=con.execute('SELECT COUNT(*) FROM category_template').fetchone()[0]
prods=con.execute('SELECT COUNT(*) FROM product_dynamic').fetchone()[0]
pend=con.execute(\"SELECT COUNT(*) FROM approval_ticket WHERE status='pending'\").fetchone()[0]
print(f'  分类={cats} 商品={prods} 待审工单={pend}')
"
echo
echo "完成。如绑定微信：管理页 →「绑定微信」→ 扫码 → 等自动落库（无需点确认）。"
