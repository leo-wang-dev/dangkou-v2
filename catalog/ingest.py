"""异步导入编排：立即返回 doc_id；后台线程 解析→四分类→工单→回调。"""
import json
import os
import threading
import traceback
import uuid

from . import agent, classify, tickets
from .templates import TEMPLATES


def start(conn, storage, xlsx_path, category, callback=None) -> int:
    if category not in TEMPLATES:
        raise ValueError(f'未知品类: {category}')
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
