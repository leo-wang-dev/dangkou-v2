"""异步导入编排：立即返回 doc_id；后台线程 解析→四分类→工单→回调。"""
import json
import os
import threading
import traceback
import uuid

from . import agent, classify, tickets, import_snapshot
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


def start(conn, storage, xlsx_path, category, callback=None, source_key=None) -> int:
    if category is not None and category not in TEMPLATES:
        raise ValueError(f'未知品类: {category}')
    xlsx_path = _ensure_xlsx(xlsx_path)
    cur = conn.execute(
        'INSERT INTO import_doc(filename, category, status, source_key) VALUES(?,?,?,?)',
        (os.path.basename(xlsx_path), category or 'auto', 'parsing', source_key or os.path.basename(xlsx_path)))
    conn.commit()
    doc_id = cur.lastrowid

    db_file = conn.execute('PRAGMA database_list').fetchone()[2]
    original_conn = conn
    def _bg():
        from . import db
        conn = db.connect(db_file) if db_file else original_conn
        try:
            work_dir = os.path.join(storage.base, '_work', f'doc{doc_id}-{uuid.uuid4().hex[:6]}')
            os.makedirs(work_dir, exist_ok=True)
            if category is None:
                from . import dynamic_import
                source = source_key or os.path.basename(xlsx_path)
                payload = dynamic_import.build_ticket_payload(
                    conn, xlsx_path, work_dir, source_key=source, doc_id=doc_id)
                tk = tickets.create(conn, 'template_import', None, payload)
                stats = {
                    'categories': [sheet['template']['name'] for sheet in payload['sheets']],
                    'new': sum(len(sheet['drafts']['new']) for sheet in payload['sheets']),
                    'update': sum(len(sheet['drafts']['update']) for sheet in payload['sheets']),
                    'delist': sum(len(sheet['drafts']['delist']) for sheet in payload['sheets']),
                }
                conn.execute("UPDATE import_doc SET status='ticketed', stats_json=? WHERE id=?",
                             (json.dumps(stats, ensure_ascii=False), doc_id))
                conn.commit()
                if callback:
                    callback(doc_id=doc_id, ticket_id=tk['id'], token=tk['token'], stats=stats)
                return
            result = agent.parse(category, xlsx_path, work_dir)
            t = TEMPLATES[category]
            source = source_key or os.path.basename(xlsx_path)
            source_rows = import_snapshot.rows(conn, category, source)
            existing = [r for r in source_rows if r['status'] != 'delisted']
            c = classify.classify(category, result['products'], existing,
                                  draft_image_root=work_dir, existing_image_root=storage.base)
            for i, d in enumerate(c['new']):
                d['_rid'] = f'n{i}'
            payload = {'kind': 'import', 'doc_id': doc_id, 'work_dir': work_dir,
                       'source_key': source, 'source_snapshot': import_snapshot.digest(source_rows),
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
        finally:
            if conn is not original_conn:
                conn.close()

    threading.Thread(target=_bg, daemon=True).start()
    return doc_id


def status(conn, doc_id) -> dict:
    r = conn.execute('SELECT * FROM import_doc WHERE id=?', (doc_id,)).fetchone()
    if r is None:
        raise KeyError(doc_id)
    return {'id': r['id'], 'status': r['status'],
            'stats': json.loads(r['stats_json'] or 'null'), 'error': r['error']}
