"""B-end notifications use the same durable outbox as customer-service handoffs.

删D 通知合一后，本模块同时是 cs_outbox 的唯一投递实现：deliver() 消费全部
notify* 渠道（notify/notify_file/notify_import），由 scripts/run_notifications.py
独立进程轮询调用；CsBot/H5 路由只写队列（_enqueue），不负责投递。
"""
import json
import os

from . import config, db


def _queue(channel, body, conn=None, *, commit=True):
    own = conn is None
    conn = conn or db.connect()
    try:
        conn.execute('INSERT INTO cs_outbox(channel,body) VALUES(?,?)', (channel, body))
        if commit or own:
            conn.commit()
    finally:
        if own:
            conn.close()
    return {'notification': 'queued'}


def push(doc_id, ticket_id, token, stats, conn=None, *, commit=True):
    if stats.get('error'):
        text = f'❌ 导入失败（doc{doc_id}）：{stats["error"]}'
    else:
        return _queue('notify_import', json.dumps({'doc_id': doc_id, 'stats': stats}, ensure_ascii=False), conn, commit=commit)
    return _queue('notify', text, conn, commit=commit)


def render_import(payload):
    # Materialize the service credential only in memory at delivery, never in the queue.
    stats = payload['stats']
    cat = '、'.join(str(name) for name in (stats.get('categories') or []))
    from urllib.parse import quote
    base = (os.environ.get('CATALOG_V2_MANAGE_URL') or
            os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')).rstrip('/')
    link = f'{base}/?t={quote(config.SERVICE_TOKEN, safe="")}'
    if stats.get('phase') == 'template' and stats.get('approved'):
        return (f'✅ 分类模板已审批通过：{stats.get("template_count", 0)} 个\n'
                '请再次上传同一份商品 Excel，系统才会开始解析和导入商品。')
    if stats.get('phase') == 'template':
        categories = '、'.join(str(value) for value in (stats.get('categories') or [])) or '新分类'
        return (f'🧩 分类模板已识别：{categories}\n'
                '请打开审批入口确认字段和客户可见性；模板审批通过后，请再次上传同一份商品 Excel，系统才会导入商品。\n'
                f'模板审批入口：{link}')
    if stats.get('phase') == 'products':
        return (f'📦 商品已解析：新增{stats.get("new", 0)} / 更新{stats.get("update", 0)} / 失败区域{stats.get("failed", 0)}\n'
                '默认增量导入，缺行保留；失败位置和覆盖范围请在审批页核对。\n'
                f'商品审批入口：{link}')
    return (f'📦 导入完成：{cat} 新增{stats.get("new", 0)} / 更新{stats.get("update", 0)} / 失败区域{stats.get("failed", 0)}\n'
            f'供应商：{stats.get("vendor") or "未提供"}\n审批入口：{link}')


def push_file(text, file_path, conn=None):
    return _queue('notify_file', json.dumps({'text': text, 'file_path': file_path}, ensure_ascii=False), conn)


# ---------- 投递（原 CsBot.flush_outbox 迁入，唯一出站投递实现） ----------

def wechat_remind(text):
    """微信推送：catalog-notify 引擎桥是唯一微信直发通道（HTTP 调用保持现状）。"""
    url = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify')
    token = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
    try:
        import requests
        response = requests.post(url, json={'text': text}, timeout=15,
                                 headers={'Authorization': f'Bearer {token}'})
        response.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f'[cs-remind] 微信提醒发送失败：{type(e).__name__}，待发送队列保留全文', flush=True)
        return False


def wechat_file(body):
    """微信文件推送：走同一个引擎桥的 /notify-file 端点。"""
    import requests
    url = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify').rstrip('/')
    token = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
    response = requests.post(url.removesuffix('/notify') + '/notify-file',
                             json=json.loads(body), timeout=30,
                             headers={'Authorization': f'Bearer {token}'})
    response.raise_for_status()


def deliver(conn, notifier=None, file_sender=None):
    """消费 cs_outbox 全部剩余渠道（notify/notify_file/notify_import）。

    run_notifications 进程的主循环体；重试按（渠道,收件人）隔离，一个渠道
    失败不阻塞其他通知。返回本轮处理行数（测试/监控用）。
    """
    notifier = notifier or wechat_remind
    file_sender = file_sender or wechat_file
    rows = conn.execute(
        "SELECT *, next_attempt_at<=datetime('now') AS due FROM cs_outbox "
        'WHERE sent=0 ORDER BY id').fetchall()
    blocked = set()
    for row in rows:
        key = (row['channel'], row['recipient'])
        if key in blocked:
            continue
        if not row['due']:
            blocked.add(key)
            continue
        try:
            if row['channel'] == 'notify_import':
                if notifier(render_import(json.loads(row['body']))) is False:
                    raise RuntimeError('通知未成功')
            elif row['channel'] == 'notify_file':
                file_sender(row['body'])
            else:
                if notifier(row['body']) is False:
                    raise RuntimeError('通知未成功')
            conn.execute('UPDATE cs_outbox SET sent=1,last_error=NULL WHERE id=?', (row['id'],))
        except Exception as exc:
            blocked.add(key)
            conn.execute("UPDATE cs_outbox SET attempts=attempts+1,last_error=?, next_attempt_at=datetime('now','+30 seconds') WHERE id=?",
                         (type(exc).__name__, row['id']))
            print(f"[notify] 待发送消息 {row['id']} 失败，已保留重试", flush=True)
        conn.commit()
    return len(rows)
