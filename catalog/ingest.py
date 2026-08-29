"""异步导入编排：立即返回 doc_id；后台线程 解析→四分类→工单→回调。"""
import json
import os
import threading
import traceback
import uuid

from . import agent, classify, tickets
from .templates import TEMPLATES


def _ensure_xlsx(path: str) -> str:
    """.xls 老格式先 soffice 转 .xlsx（保内嵌图；微信常发 .xls，openpyxl 读不了）。"""
    if not path.lower().endswith('.xls'):
        return path
    conv = path.rsplit('.', 1)[0] + '.xlsx'
    if not os.path.exists(conv):
        import subprocess
        subprocess.run(['soffice', '--headless', '--convert-to', 'xlsx', '--outdir',
                        os.path.dirname(path) or '.', path],
                       capture_output=True, timeout=600)
    if not os.path.exists(conv):
        raise RuntimeError('老 .xls 转换失败（服务器 soffice）')
    return conv


def start(conn, storage, xlsx_path, category, callback=None) -> int:
    if category not in TEMPLATES:
        raise ValueError(f'未知品类: {category}')
    xlsx_path = _ensure_xlsx(xlsx_path)
    cur = conn.execute(
        'INSERT INTO import_doc(filename, category, status) VALUES(?,?,?)',
        (os.path.basename(xlsx_path), category, 'parsing'))
    conn.commit()
    doc_id = cur.lastrowid

    def _bg():
        try:
            work_dir = os.path.join(storage.base, '_work', f'doc{doc_id}-{uuid.uuid4().hex[:6]}')
            os.makedirs(work_dir, exist_ok=True)
            result = agent.parse(category, xlsx_path, work_dir)
            t = TEMPLATES[category]
            existing = conn.execute(
                f'SELECT * FROM {t.table} WHERE status != "delisted"').fetchall()
            c = classify.classify(category, result['products'], existing)
            for i, d in enumerate(c['new']):
                d['_rid'] = f'n{i}'
            payload = {'kind': 'import', 'doc_id': doc_id, 'work_dir': work_dir,
                       'drafts': {'new': c['new'],
                                  'update': [[dict(r), d] for r, d in c['update']],
                                  'delist': [dict(r) for r in c['delist']]}}
            tk = tickets.create(conn, 'import', category, payload)
            stats = {'new': len(c['new']), 'update': len(c['update']), 'category': category,
                     'delist': len(c['delist']), 'vendor': result.get('vendor')}
            conn.execute("UPDATE import_doc SET status='ticketed', stats_json=? WHERE id=?",
                         (json.dumps(stats, ensure_ascii=False), doc_id))
            conn.commit()
            if callback:
                callback(doc_id=doc_id, ticket_id=tk['id'], token=tk['token'], stats=stats)
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE import_doc SET status='failed', error=? WHERE id=?",
                         (str(e)[:300], doc_id))
            conn.commit()
            traceback.print_exc()
            if callback:
                callback(doc_id=doc_id, ticket_id=None, token=None,
                         stats={'error': str(e)[:200]})

    threading.Thread(target=_bg, daemon=True).start()
    return doc_id


def status(conn, doc_id) -> dict:
    r = conn.execute('SELECT * FROM import_doc WHERE id=?', (doc_id,)).fetchone()
    if r is None:
        raise KeyError(doc_id)
    return {'id': r['id'], 'status': r['status'],
            'stats': json.loads(r['stats_json'] or 'null'), 'error': r['error']}
