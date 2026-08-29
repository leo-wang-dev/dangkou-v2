"""完成即回流：解析完成/失败 → POST 引擎 catalog-notify → followup 微信会话主动推送。"""
import json
import os

import requests

from . import config

NOTIFY_URL = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify')
NOTIFY_TOKEN = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
PUBLIC_URL = os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')


def push(doc_id, ticket_id, token, stats):
    """ingest 回调：stats 有 error=失败通知，否则成功通知（带审批链接）。"""
    if not NOTIFY_TOKEN:
        return  # 未配置则静默跳过（本地测试环境）
    if stats.get('error'):
        text = (f'[系统通知·导入失败] 文档doc{doc_id} 解析失败：{stats["error"]}。'
                f'请立即用 im_send 工具把失败原因告知用户，语气抱歉，别让用户干等。')
    else:
        link = f'{PUBLIC_URL}/?t={config.SERVICE_TOKEN}'
        text = (f'[系统通知·导入完成] 文档doc{doc_id} 解析完成：'
                f'新增{stats.get("new", 0)}/更新{stats.get("update", 0)}/'
                f'下架{stats.get("delist", 0)}'
                f'{"（厂家：" + stats["vendor"] + "）" if stats.get("vendor") else ""}。\n'
                f'请立即用 im_send 工具发消息给用户：简短告知完成统计，'
                f'并附审批链接 {link} ，提醒用户点开审批。不要调用其他工具。')
    try:
        r = requests.post(NOTIFY_URL, json={'text': text}, timeout=15,
                          headers={'Authorization': f'Bearer {NOTIFY_TOKEN}'})
        print(f'[notify] 回流推送 rc={r.status_code} {r.text[:80]}', flush=True)
    except Exception as e:  # noqa: BLE001
        print(f'[notify] 回流推送失败: {e}', flush=True)
