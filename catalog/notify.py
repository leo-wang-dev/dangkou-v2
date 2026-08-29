"""完成即推送：解析完成/失败 → 引擎 catalog-notify 直连微信发送器（不过模型）。"""
import os

import requests

from . import config

NOTIFY_URL = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify')
NOTIFY_TOKEN = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
PUBLIC_FALLBACK = 'http://134.175.135.102:8890'  # 教训：默认绝不能是本地地址

CAT_NAME = {'razor': '剃须刀', 'curler': '卷发棒'}


def push(doc_id, ticket_id, token, stats):
    """ingest 回调：直推用户可读的完成/失败通知（带审批入口）。"""
    if not NOTIFY_TOKEN:
        return
    if stats.get('error'):
        text = f'❌ 导入失败（doc{doc_id}）：{stats["error"][:120]}'
    else:
        cat = CAT_NAME.get(stats.get('category'), '')
        link = f"{os.environ.get('CATALOG_V2_PUBLIC_URL', PUBLIC_FALLBACK)}/?t={config.SERVICE_TOKEN}'"
        text = (f'📦 导入完成：{cat} 新增{stats.get("new", 0)} / '
                f'更新{stats.get("update", 0)} / 下架{stats.get("delist", 0)}'
                f'{"（" + stats["vendor"] + "）" if stats.get("vendor") else ""}\n'
                f'审批入口：{link}\n（点开即可逐行审批）')
    try:
        r = requests.post(NOTIFY_URL, json={'text': text}, timeout=15,
                          headers={'Authorization': f'Bearer {NOTIFY_TOKEN}'})
        print(f'[notify] 直推 rc={r.status_code} {r.text[:80]}', flush=True)
    except Exception as e:  # noqa: BLE001
        print(f'[notify] 直推失败: {e}', flush=True)
