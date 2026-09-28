"""Durable, leased import jobs. SQLite fences ticket/status/outbox finalization."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from . import db, tickets, notify

LEASE_SECONDS = 90
_start_lock = threading.RLock()
_workers = set()


def join_workers(timeout=30):
    deadline = time.monotonic() + timeout
    while _workers and time.monotonic() < deadline:
        for worker in list(_workers):
            worker.join(max(0, deadline - time.monotonic()))
    return not _workers


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_xlsx(path):
    if not str(path).lower().endswith('.xls'):
        return str(path)
    import subprocess
    directory = tempfile.mkdtemp(prefix='converted-', dir=os.path.dirname(path) or '.')
    result = subprocess.run(['soffice', '-env:UserInstallation=file://' + directory + '/profile',
        '--headless', '--convert-to', 'xlsx', '--outdir', directory, str(path)],
        capture_output=True, timeout=600)
    converted = os.path.join(directory, Path(path).stem + '.xlsx')
    if result.returncode or not os.path.isfile(converted):
        raise RuntimeError('老 .xls 转换失败（服务器 soffice），请重试')
    return converted


def _claim(conn, doc_id):
    """A short DB-wide admission transaction precedes thread/container creation."""
    now = time.time()
    owner = uuid.uuid4().hex
    conn.execute('BEGIN IMMEDIATE')
    try:
        active = conn.execute("SELECT count(*) FROM import_doc WHERE status='parsing' AND lease_until>?",(now,)).fetchone()[0]
        if active >= max(1,int(os.environ.get('CATALOG_IMPORT_WORKERS','1'))):
            conn.rollback()
            return None
        changed = conn.execute("UPDATE import_doc SET lease_owner=?,lease_until=?,attempt=attempt+1,progress_json=? WHERE id=? AND status='parsing' AND lease_until<=?",
            (owner,now+LEASE_SECONDS,json.dumps({'stage':'preparing'}),doc_id,now)).rowcount
        conn.commit()
        return owner if changed else None
    except BaseException:
        conn.rollback()
        raise


def _launch(conn, doc_id, callback=None):
    db_file = conn.execute('PRAGMA database_list').fetchone()[2]
    with _start_lock:
        owner = _claim(conn,doc_id)
        if not owner:
            return False
        if not db_file:
            _run(conn, doc_id, callback, owner=owner)
            return True
        def run():
            owned = db.connect(db_file)
            try:
                _run(owned,doc_id,callback,owner=owner)
            finally:
                try:
                    # Drain accepted jobs without a polling thread per queued upload.
                    recover(owned)
                finally:
                    owned.close()
                    _workers.discard(threading.current_thread())
        worker = threading.Thread(target=run,daemon=True,name=f'import-{doc_id}')
        _workers.add(worker)
        worker.start()
        return True


def start(conn, storage, xlsx_path, callback=None, source_key=None,
          mode=None, category_key=None, *, phase='template', template_doc_id=None):
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
    source = source_key or os.path.basename(xlsx_path)
    content_hash = _sha256(xlsx_path)
    with _start_lock:
        conn.execute('BEGIN IMMEDIATE')
        try:
            duplicate = conn.execute(
                "SELECT id FROM import_doc WHERE source_key=? AND content_sha256=? "
                "AND mode=? AND category_key=? AND phase=? AND "
                "COALESCE(template_doc_id,0)=COALESCE(?,0) AND (status='parsing' OR "
                "(status='ticketed' AND EXISTS (SELECT 1 FROM approval_ticket t "
                "WHERE t.status='pending' AND json_extract(t.payload,'$.doc_id')=import_doc.id))) "
                "ORDER BY id DESC LIMIT 1",
                (source, content_hash, mode or '', category_key or '', phase, template_doc_id)).fetchone()
            if duplicate:
                conn.commit()
                _launch(conn, duplicate['id'], callback)
                return duplicate['id']
            directory = Path(storage.base).resolve() / '_imports' / uuid.uuid4().hex
            directory.mkdir(parents=True)
            owned = directory / ('source' + Path(xlsx_path).suffix.lower())
            shutil.copyfile(xlsx_path, owned)
            if _sha256(owned) != content_hash:
                raise ValueError('上传文件在复制时发生变化，请重试')
            cur = conn.execute(
                'INSERT INTO import_doc(filename,category,source_key,mode,category_key,content_sha256,phase,template_doc_id,input_path,storage_base,work_dir) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (os.path.basename(xlsx_path), 'auto', source, mode or '', category_key or '',
                 content_hash, phase, template_doc_id, str(owned), str(Path(storage.base).resolve()), str(directory / 'work')))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _launch(conn, cur.lastrowid, callback)
        return cur.lastrowid


def recover(conn):
    """Restart only expired jobs; a database lease arbitrates across processes."""
    rows = conn.execute("SELECT id FROM import_doc WHERE status='parsing' AND lease_until<=? ORDER BY id", (time.time(),)).fetchall()
    for row in rows:
        if _launch(conn, row['id']) is False:
            break  # Capacity is full; the existing recovery poll retries later.
    return len(rows)


def _run(conn, doc_id, callback=None, *, owner=None):
    owner = owner or _claim(conn,doc_id)
    if not owner:
        return
    row = dict(conn.execute('SELECT * FROM import_doc WHERE id=?', (doc_id,)).fetchone())
    stop = threading.Event()
    db_file = conn.execute('PRAGMA database_list').fetchone()[2]
    def heartbeat():
        connection = db.connect(db_file)
        try:
            while not stop.wait(LEASE_SECONDS / 3):
                connection.execute("UPDATE import_doc SET lease_until=? WHERE id=? AND lease_owner=? AND status='parsing'", (time.time()+LEASE_SECONDS, doc_id, owner))
                connection.commit()
        finally:
            connection.close()
    pulse = threading.Thread(target=heartbeat, daemon=True) if db_file else None
    if pulse:
        pulse.start()
    event = None
    try:
        from . import dynamic_import
        phase = row['phase']
        error = None
        payload = None
        try:
            if not row['input_path']:
                raise ValueError('旧导入任务缺少持久源文件，请重新上传 Excel')
            if _sha256(row['input_path']) != row['content_sha256']:
                raise ValueError('导入源文件校验失败，请重新上传')
            path = row['converted_path']
            if not path or not os.path.isfile(path) or _sha256(path) != row['converted_sha256']:
                path = _ensure_xlsx(row['input_path'])
                conn.execute('UPDATE import_doc SET converted_path=?,converted_sha256=? WHERE id=? AND lease_owner=?',
                    (path, _sha256(path), doc_id, owner)); conn.commit()
            work_dir = os.path.join(row['work_dir'], 'attempt-' + owner)
            os.makedirs(work_dir, exist_ok=True)
            conn.execute('UPDATE import_doc SET progress_json=? WHERE id=? AND lease_owner=?',
                (json.dumps({'stage': 'parsing', 'phase': phase}), doc_id, owner)); conn.commit()
            kwargs = dict(source_key=row['source_key'], doc_id=doc_id,
                          mode=row['mode'] or None, category_key=row['category_key'] or None)
            if phase == 'template':
                payload = dynamic_import.build_template_payload(conn, path, work_dir, **kwargs)
                stats = {'phase': phase, 'template_doc_id': doc_id,
                    'categories': [s['template']['name'] for s in payload['sheets']], 'next_action': 'upload_products'}
            else:
                payload = dynamic_import.build_product_payload(conn, path, work_dir,
                    template_doc_id=row['template_doc_id'], **kwargs)
                failures = [f for s in payload['sheets'] for f in s.get('failures', [])]
                stats = {'phase': phase, 'template_doc_id': row['template_doc_id'],
                    **{key: sum(len(s['drafts'][key]) for s in payload['sheets']) for key in ('new','update','delist')},
                    'failed': len(failures), 'failures': failures,
                    'coverage': [s.get('coverage', {}) for s in payload['sheets']]}
            payload['filename'] = row['filename']
        except Exception as exc:
            error = str(exc)[:500]
            stats = {'phase': phase, 'error': error, 'retryable': True}
        # Ticket creation and the no-secret durable outbox event are one commit.
        # Failure here leaves the leased job recoverable, never a half-ticket.
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute("SELECT lease_owner,status FROM import_doc WHERE id=?", (doc_id,)).fetchone()
        if current['lease_owner'] != owner or current['status'] != 'parsing':
            conn.rollback()
            return
        tk = tickets.create(conn, phase[:-1] + '_import' if phase == 'products' else 'template_import', None, payload, commit=False) if payload and not error else None
        if tk:
            stats['product_ticket_id' if phase == 'products' else 'template_ticket_id'] = tk['id']
        event = dict(doc_id=doc_id, ticket_id=tk['id'] if tk else None,
                     token=tk['token'] if tk else None, stats=stats)
        notify.push(**event, conn=conn, commit=False)
        conn.execute('UPDATE import_doc SET status=?,stats_json=?,error=?,lease_until=0,progress_json=? WHERE id=? AND lease_owner=?',
            ('failed' if error else 'ticketed', json.dumps(stats, ensure_ascii=False), error,
             json.dumps({'stage': 'failed' if error else 'complete'}), doc_id, owner))
        conn.commit()
    except Exception:
        conn.rollback()
        event = None
        # A later startup/poll reclaims this attempt after its lease expires.
    finally:
        stop.set()
        if pulse:
            pulse.join()
    if event and callback and callback is not notify.push:
        try:
            callback(**event)
        except Exception:
            pass  # Durable event already exists. Optional observer cannot undo it.


def status(conn, doc_id) -> dict:
    r = conn.execute('SELECT * FROM import_doc WHERE id=?', (doc_id,)).fetchone()
    if r is None:
        raise KeyError(doc_id)
    return {'id': r['id'], 'status': r['status'], 'phase': r['phase'],
            'template_doc_id': r['template_doc_id'],
            'progress': json.loads(r['progress_json'] or '{}'), 'attempt': r['attempt'],
            'stats': json.loads(r['stats_json'] or 'null'), 'error': r['error']}
