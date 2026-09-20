"""B-end notifications use the same durable outbox as customer-service handoffs."""
import json
import os

from . import config, db

CAT_NAME = {'razor': '剃须刀', 'curler': '卷发棒'}


def _queue(channel, body, conn=None):
    own = conn is None
    conn = conn or db.connect()
    try:
        conn.execute('INSERT INTO cs_outbox(channel,body) VALUES(?,?)', (channel, body))
        conn.commit()
    finally:
        if own:
            conn.close()
    return {'notification': 'queued'}


def push(doc_id, ticket_id, token, stats, conn=None):
    if stats.get('error'):
        text = f'❌ 导入失败（doc{doc_id}）：{stats["error"]}'
    else:
        return _queue('notify_import', json.dumps({'doc_id': doc_id, 'stats': stats}, ensure_ascii=False), conn)
    return _queue('notify', text, conn)


def render_import(payload):
    # Materialize the service credential only in memory at delivery, never in the queue.
    stats = payload['stats']
    cat = CAT_NAME.get(stats.get('category'), '')
    from urllib.parse import quote
    base = (os.environ.get('CATALOG_V2_MANAGE_URL') or
            os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')).rstrip('/')
    link = f'{base}/?t={quote(config.SERVICE_TOKEN, safe="")}'
    return (f'📦 导入完成：{cat} 新增{stats.get("new", 0)} / 更新{stats.get("update", 0)} / 下架{stats.get("delist", 0)}\n'
            f'供应商：{stats.get("vendor") or "未提供"}\n审批入口：{link}')


def push_file(text, file_path, conn=None):
    return _queue('notify_file', json.dumps({'text': text, 'file_path': file_path}, ensure_ascii=False), conn)
