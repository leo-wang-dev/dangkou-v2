"""异步导入编排：立即返回 doc_id；后台线程 解析→四分类→工单→回调。"""
import json
import hashlib
import os
import threading
import traceback
import uuid

from . import tickets


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


_start_lock = threading.Lock()


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def start(conn, storage, xlsx_path, callback=None, source_key=None,
          mode=None, category_key=None, *, phase='template', template_doc_id=None) -> int:
    """两阶段动态分类导入：phase=template 只建模板工单；phase=products 按已审批模板导入商品。"""
    if mode not in (None, 'new', 'existing'):
        raise ValueError('导入 mode 只能是 new 或 existing')
    if mode == 'existing' and not category_key:
        raise ValueError('并入已有分类时必须提供 category_key')
    if mode != 'existing' and category_key and not (phase == 'products' and not template_doc_id):
        raise ValueError('只有 existing 模式可以提供 category_key')
    if phase not in ('template', 'products'):
        raise ValueError('导入 phase 只能是 template 或 products')
    if phase == 'products' and not template_doc_id and not category_key:
        raise ValueError('商品导入必须提供已审批的 template_doc_id 或已有分类的 category_key')
    if phase != 'products' and template_doc_id:
        raise ValueError('只有 products 阶段可以提供 template_doc_id')
    xlsx_path = _ensure_xlsx(xlsx_path)
    source = source_key or os.path.basename(xlsx_path)
    # A repeated model/tool call for the same bytes must reuse the in-flight or
    # pending import. Reimports with changed bytes still get a new document.
    content_sha256 = _sha256(xlsx_path) if os.path.isfile(xlsx_path) else ''
    with _start_lock:
        duplicate = None
        if content_sha256:
            duplicate = conn.execute(
                "SELECT id FROM import_doc WHERE source_key=? AND content_sha256=? "
                "AND mode=? AND category_key=? AND phase=? AND "
                "COALESCE(template_doc_id,0)=COALESCE(?,0) AND (status='parsing' OR "
                "(status='ticketed' AND EXISTS (SELECT 1 FROM approval_ticket t "
                " WHERE t.status='pending' AND json_extract(t.payload,'$.doc_id')=import_doc.id))) "
                "ORDER BY id DESC LIMIT 1",
                (source, content_sha256, mode or '', category_key or '', phase, template_doc_id)).fetchone()
        if duplicate:
            return duplicate['id']
        cur = conn.execute(
            'INSERT INTO import_doc(filename, category, status, source_key, mode, category_key, content_sha256, phase, template_doc_id) '
            'VALUES(?,?,?,?,?,?,?,?,?)',
            (os.path.basename(xlsx_path), 'auto', 'parsing', source,
             mode or '', category_key or '', content_sha256, phase, template_doc_id))
        conn.commit()
        doc_id = cur.lastrowid

    db_file = conn.execute('PRAGMA database_list').fetchone()[2]
    original_conn = conn
    def _bg():
        from . import db
        conn = db.connect(db_file) if db_file else original_conn
        try:
            source = source_key or os.path.basename(xlsx_path)
            work_dir = os.path.join(storage.base, '_work', f'doc{doc_id}-{uuid.uuid4().hex[:6]}')
            os.makedirs(work_dir, exist_ok=True)
            from . import dynamic_import
            if phase == 'template':
                payload = dynamic_import.build_template_payload(
                    conn, xlsx_path, work_dir, source_key=source, doc_id=doc_id,
                    mode=mode, category_key=category_key)
                tk = tickets.create(conn, 'template_import', None, payload)
                stats = {
                    'phase': 'template', 'template_doc_id': doc_id,
                    'categories': [sheet['template']['name'] for sheet in payload['sheets']],
                    'next_action': 'upload_products', 'template_ticket_id': tk['id'],
                }
            else:
                payload = dynamic_import.build_product_payload(
                    conn, xlsx_path, work_dir, source_key=source,
                    template_doc_id=template_doc_id, doc_id=doc_id,
                    mode=mode, category_key=category_key)
                tk = tickets.create(conn, 'product_import', None, payload)
                stats = {
                    'phase': 'products', 'template_doc_id': template_doc_id,
                    'new': sum(len(sheet['drafts']['new']) for sheet in payload['sheets']),
                    'update': sum(len(sheet['drafts']['update']) for sheet in payload['sheets']),
                    'delist': sum(len(sheet['drafts']['delist']) for sheet in payload['sheets']),
                    'product_ticket_id': tk['id'],
                }
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
    return {'id': r['id'], 'status': r['status'], 'phase': r['phase'],
            'template_doc_id': r['template_doc_id'],
            'stats': json.loads(r['stats_json'] or 'null'), 'error': r['error']}
