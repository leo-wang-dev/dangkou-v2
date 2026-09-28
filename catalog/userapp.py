"""平台级中央用户工具服务（新G，2026-09-27 老板拍板）。

纯「照片 → AI 抽取 → 清单 → 导出 Excel」：不连任何档口商品库、不走客服
persona 链路，与档口库完全隔离（独立 SQLite，独立进程独立端口）。用户体系
= 邮箱验证码（发送暂为桩：日志 + data/userapp-codes.log，真渠道明天接）；
游客 guest-<hex> 可直接用，登录时把当前游客的记录合并进账号。

抽取链复用 catalog.csbot.extract_photo_items（EXTRACT_PROMPT/解析/复审/
名片分流）与 CsBot._crop_photo（图框裁剪）——提示词零复制，两端口径一致。
导出复用 cs_export.render_notes（同一列契约：无确认状态列）。
"""
import hashlib
import asyncio
import json
import os
import re
import secrets
import sqlite3
import threading
from contextvars import ContextVar

import anyio

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from . import guest_sessions, note_batches
from . import llm as default_llm
from .cs_export import render_notes
from . import cs_i18n
from .cs_supplier import normalize as normalize_fields
from .csbot import CsBot, extract_photo_items
from .request_lifecycle import drain_worker

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+\Z')
CODE_TTL_MINUTES = 10
SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(
  email TEXT PRIMARY KEY,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS auth_codes(
  email TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_kind TEXT NOT NULL CHECK(owner_kind IN ('user','guest')),
  owner_id TEXT NOT NULL,
  photo_path TEXT NOT NULL DEFAULT '',
  fields_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner_kind, owner_id);
CREATE TABLE IF NOT EXISTS session_tokens(
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')));
'''


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _send_code_stub(email: str, code: str, codes_log: str):
    """验证码发送：Resend 真渠道（受限 key 只发信）；未配 key 时回落明文日志桩。"""
    api_key = os.environ.get('RESEND_API_KEY', '')
    sender = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
    if api_key:
        import requests
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {api_key}'},
            json={'from': sender, 'to': [email],
                  'subject': '你的登录验证码',
                  'text': f'验证码：{code}\n10 分钟内有效。若非本人操作请忽略。'},
            timeout=15)
        if r.status_code not in (200, 201):
            raise RuntimeError(f'resend {r.status_code}: {r.text[:200]}')
        print(f'[userapp] auth code sent via resend -> {email}', flush=True)
        return
    line = f'{email}\t{code}'
    print(f'[userapp] auth code(stub) -> {line}')
    os.makedirs(os.path.dirname(os.path.abspath(codes_log)), exist_ok=True)
    with open(codes_log, 'a', encoding='utf-8') as fh:
        fh.write(line + '\n')


def build_app(db_path=None, photo_dir=None, codes_log=None, llm=None):
    """构建独立 FastAPI app；模块导入零副作用（测试传 tmp 路径直建）。"""
    db_path = db_path or os.environ.get(
        'USER_APP_DB', os.path.join(_ROOT, 'data', 'userapp.db'))
    photo_dir = photo_dir or os.environ.get(
        'USER_APP_PHOTOS', os.path.join(_ROOT, 'data', 'userapp_photos'))
    codes_log = codes_log or os.environ.get(
        'USER_APP_CODES_LOG', os.path.join(_ROOT, 'data', 'userapp-codes.log'))
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    os.makedirs(photo_dir, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    guest_sessions.migrate(conn)
    note_batches.migrate(conn, "notes")
    for table in ('users','guest_sessions'):
        if 'lang' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
    conn.commit()

    app = FastAPI(title='dangkou user tool')
    cs_i18n.install_errors(app)
    app.state.conn = conn
    app.state.photo_dir = photo_dir
    app.state.codes_log = codes_log
    app.state.llm = llm or default_llm
    current_connection = ContextVar('userapp_request_connection', default=None)
    model_limiter = anyio.CapacityLimiter(4)

    def request_conn():
        return current_connection.get() or conn

    async def run_request_worker(request, work, *, model=False):
        done = threading.Event()
        request.state.database_worker_done = done
        try:
            return await anyio.to_thread.run_sync(
                work, limiter=model_limiter if model else None)
        finally:
            done.set()

    @app.middleware('http')
    async def database_request(request, call_next):
        connection = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
        connection.row_factory = sqlite3.Row
        context = current_connection.set(connection)

        async def wait_for_worker():
            done = getattr(request.state, 'database_worker_done', None)
            return await drain_worker(done)

        try:
            # A separate, short cleanup transaction commits before endpoint/model work.
            cleanup = sqlite3.connect(db_path)
            cleanup.row_factory = sqlite3.Row
            try:
                paths = purge_guests(cleanup)
                cleanup.commit()
                remove_unreferenced(cleanup, paths)
            finally:
                cleanup.close()
            try:
                response = await call_next(request)
                interrupted = await wait_for_worker()
                if interrupted or asyncio.current_task().cancelling():
                    raise asyncio.CancelledError()
                if response.status_code >= 400:
                    connection.rollback()
                else:
                    capability = getattr(request.state, 'guest_capability', '')
                    if capability:
                        try:
                            guest_sessions.touch(connection, capability)
                        except HTTPException as exc:
                            connection.rollback()
                            from fastapi.responses import JSONResponse
                            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
                    paths = purge_guests(connection)
                    connection.commit()
                    remove_unreferenced(connection, paths)
                return response
            except BaseException:
                await wait_for_worker()
                connection.rollback()
                raise
        finally:
            remove_unreferenced(connection, getattr(request.state, 'created_photos', []))
            current_connection.reset(context)
            connection.close()

    # C端无 Cookie（身份=游客参数/链接token），放开跨域供 uni-app H5 开发期
    # 直连与调试工具使用；同源部署的旧页行为不变。非浏览器端（小程序）无 CORS 概念。
    app.add_middleware(
        CORSMiddleware, allow_origins=['*'],
        allow_methods=['GET', 'POST', 'OPTIONS'], allow_headers=['*'],
        expose_headers=['Content-Disposition'])

    # ---------- 身份：bearer（邮箱登录）或 guest= 参数（游客），手写解析 ----

    def _identity(request: Request, guest: str = ''):
        auth = request.headers.get('authorization', '')
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
        token = token or str(request.query_params.get('token') or '')
        if token:
            row = request_conn().execute(
                'SELECT email FROM session_tokens WHERE token_hash=?',
                (_hash(token),)).fetchone()
            if row is None:
                raise HTTPException(401, '登录已失效，请重新登录')
            language=request_conn().execute('SELECT lang FROM users WHERE email=?',(row['email'],)).fetchone()
            request.state.customer_language=cs_i18n.normalize_language(language[0] if language else '')
            return 'user', row['email']
        guest = str(guest or request.query_params.get('guest') or '').strip()
        if guest:
            session = guest_sessions.validate(request_conn(), guest)
            request.state.customer_language = session['lang']
            request.state.guest_capability = guest
            return 'guest', session['owner_id']
        raise HTTPException(401, '缺少身份：请登录，或刷新页面以游客模式使用')

    def remove_unreferenced(connection, paths):
        for path in set(paths):
            if connection.execute('SELECT 1 FROM notes WHERE photo_path=?', (path,)).fetchone():
                continue
            if os.path.commonpath([os.path.abspath(path), os.path.abspath(photo_dir)]) != os.path.abspath(photo_dir):
                continue
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    purge_guests = guest_sessions.purge_central

    def _save_photo(data: bytes) -> str:
        fname = f'{secrets.token_hex(8)}.jpg'
        path = os.path.join(photo_dir, fname)
        open(path, 'wb').write(data)
        return path

    def _receipt(items, cards) -> str:
        """回执文本（与档口客服同款字段口径，但不做本店商品匹配/转人工）。"""
        lines = []
        for i, fields in enumerate(items, 1):
            got = [f'{k}={v}' for k, v in fields.items()
                   if v and '未拍到' not in str(v) and '模糊' not in str(v)
                   and k != '__图框__']
            miss = [k for k, v in fields.items()
                    if ('未拍到' in str(v) or '模糊' in str(v)) and k != '__图框__']
            line = f'【{i}】' + '；'.join(got)
            if miss:
                line += f'\n　没拍到/看不清：{"、".join(miss)}'
            lines.append(line)
        for card in cards:
            clean = {k: str(v or '').strip() for k, v in (card or {}).items()
                     if k in ('档口名称', '供应商联系人', '供应商联系方式', '档口号/地址')
                     and str(v or '').strip() and '未拍到' not in str(v)}
            if clean:
                lines.append('已识别名片，待确认切换档口：' + '；'.join(f'{k}={v}' for k, v in clean.items()))
        body = ('整理好了，已加入清单：\n' + '\n'.join(lines)
                + '\n\n可继续拍照累积；点底部「📋 清单」查看，「⬇ Excel」随时导出。'
                '\n照片里的价格只是记录，不是报价。')
        return body

    # ---------- 游客 ----------

    @app.post('/guest')
    def new_guest(request: Request):
        guest=guest_sessions.issue(request_conn())
        request_conn().execute('UPDATE guest_sessions SET lang=? WHERE token_hash=?',(cs_i18n.request_language(request),_hash(guest)))
        return {'guest': guest, 'photo_mode': 'notes', 'idle_seconds': guest_sessions.ttl()}

    @app.post('/lang')
    async def set_language(request: Request):
        body = await request.json()
        kind, owner = _identity(request,str(body.get('guest') or ''))
        lang = cs_i18n.normalize_language(body.get('lang'))
        table, key = ('users','email') if kind == 'user' else ('guest_sessions','owner_id')
        request_conn().execute(f'UPDATE {table} SET lang=? WHERE {key}=?',(lang,owner))
        return {'lang':lang}

    @app.post('/session/end')
    async def end_session(request: Request):
        body = await request.json() if request.headers.get('content-type', '').startswith('application/json') else {}
        kind, owner = _identity(request, str(body.get('guest') or ''))
        if kind == 'guest':
            guest_sessions.revoke(request_conn(), request.state.guest_capability)
            request.state.guest_capability = ''
        else:
            auth = request.headers.get('authorization', '')
            request_conn().execute('DELETE FROM session_tokens WHERE token_hash=?', (_hash(auth[7:].strip() or str(request.query_params.get('token') or '')),))
        return {'ended': True}

    # ---------- 拍照抽取 ----------

    @app.post('/photo')
    async def upload_photo(request: Request):
        form = await request.form()
        up = form.get('file')
        owner = str(form.get('owner') or '')
        if up is None or not hasattr(up, 'read'):
            raise HTTPException(400, '请上传 file 文件')
        data = await up.read()
        if not data:
            raise HTTPException(400, '文件为空')
        def process_photo():
            kind, owner_id = _identity(request, owner)
            path = _save_photo(data)
            request.state.created_photos = [path]
            items, cards = extract_photo_items(request.app.state.llm, data)
            cards = [c for c in cards if isinstance(c, dict)
                     and any(str(v or '').strip() for v in c.values())]
            if not items and not cards:
                os.unlink(path)
                return {'reply': cs_i18n.t('photoEmpty',cs_i18n.request_language(request)), 'added': 0}
            _identity(request, owner)  # expiry/revocation during model work cannot resurrect data
            batch_id, pending = note_batches.prepare(request_conn(), kind, owner_id, cards)
            start = request_conn().execute(
                'SELECT COUNT(*) FROM notes WHERE owner_kind=? AND owner_id=?',
                (kind, owner_id)).fetchone()[0] + 1
            for i, fields in enumerate(items, start):
                fields = normalize_fields(fields)
                note_photo = CsBot._crop_photo(path, i - start, fields.pop('__图框__', None))
                request.state.created_photos.append(note_photo)
                if note_photo == path:
                    fields['图片提示'] = '商品框缺失或无效，暂用整张照片'
                request_conn().execute(
                    'INSERT INTO notes(owner_kind, owner_id, photo_path, fields_json, batch_id) '
                    'VALUES(?,?,?,?,?)',
                    (kind, owner_id, note_photo, json.dumps(fields, ensure_ascii=False),batch_id))
            return {'reply': _receipt(items, cards) if cs_i18n.request_language(request)=='zh' else cs_i18n.receipt(items,cards,cs_i18n.request_language(request)), 'added': len(items), 'photo_mode':'notes',
                    'pending_batches':[b for b in note_batches.listing(request_conn(),kind,owner_id) if b['id'] in pending]}

        return await run_request_worker(request, process_photo, model=True)

    @app.post('/batches/confirm')
    async def confirm_batch(request: Request, guest: str = ''):
        kind, owner = _identity(request, guest)
        body = await request.json()
        if body.get('action') == 'decline':
            note_batches.decline(request_conn(),kind,owner,body.get('batch_id'))
            return {'declined':True}
        note_batches.confirm(request_conn(),kind,owner,body.get('batch_id'),body.get('note_ids') or [],'notes')
        return {'confirmed':True}

    @app.post('/batches')
    async def manual_batch(request: Request, guest: str = ''):
        kind, owner = _identity(request, guest)
        body = await request.json()
        bid = note_batches.create(request_conn(),kind,owner,body.get('fields'),'pending','manual')
        note_batches.confirm(request_conn(),kind,owner,bid,body.get('note_ids') or [],'notes')
        return {'batch_id':bid}

    # ---------- 清单 / 导出 ----------

    @app.post('/notes')
    def list_notes(request: Request, guest: str = ''):
        kind, owner_id = _identity(request, guest)
        rows = request_conn().execute(
            'SELECT * FROM notes WHERE owner_kind=? AND owner_id=? ORDER BY id',
            (kind, owner_id)).fetchall()
        notes = []
        for n in rows:
            n = note_batches.project(request_conn(), n)
            notes.append({'id': n['id'], 'created_at': n['created_at'],
                          'fields': json.loads(n['fields_json']), 'batch_id':n['batch_id'], 'batch_state':n['batch_state'],
                          'photo': (f'/notes/{n["id"]}/photo' if n['photo_path'] else '')})
        return {'lang':cs_i18n.request_language(request), 'notes': notes, 'batches':note_batches.listing(request_conn(),kind,owner_id)}

    @app.get('/notes/{note_id}/photo')
    def note_photo(note_id: int, request: Request, guest: str = ''):
        kind, owner_id = _identity(request, guest)
        row = request_conn().execute('SELECT photo_path FROM notes WHERE id=? AND owner_kind=? AND owner_id=?',
                           (note_id, kind, owner_id)).fetchone()
        if row is None or not row['photo_path'] or not os.path.isfile(row['photo_path']):
            raise HTTPException(404, 'no photo')
        return Response(content=open(row['photo_path'], 'rb').read(), media_type='image/jpeg',
                        headers={'Cache-Control': 'private, no-store'})

    @app.get('/export.xlsx')
    def export_xlsx(request: Request, guest: str = ''):
        kind, owner_id = _identity(request, guest)
        rows = request_conn().execute(
            'SELECT * FROM notes WHERE owner_kind=? AND owner_id=? ORDER BY id',
            (kind, owner_id)).fetchall()
        if not rows:
            raise HTTPException(409, '暂无可导出条目，请先上传照片')
        content = render_notes(
            [{**note_batches.project(request_conn(), n), 'photo': n['photo_path']} for n in rows],lang=cs_i18n.request_language(request),
            conn=request_conn(),llm=request.app.state.llm)
        fname = f'tool-list-{owner_id.split("@")[0][:16]}.xlsx'
        return Response(
            content=content,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    # ---------- 邮箱验证码（发送=桩） ----------

    @app.post('/auth/code')
    async def auth_code(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, '请求必须为 JSON 对象')
        email = str((body or {}).get('email') or '').strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise HTTPException(400, '邮箱格式不正确')
        def send_code():
            code = f'{secrets.randbelow(1000000):06d}'
            request_conn().execute(
                "INSERT INTO auth_codes(email, code_hash, expires_at) "
                "VALUES(?,?,datetime('now', ?))",
                (email, _hash(f'{email}:{code}'), f'+{CODE_TTL_MINUTES} minutes'))
            _send_code_stub(email, code, codes_log)
            return {'sent': True}   # 不回显验证码，统一“已发送”
        return await run_request_worker(request, send_code)

    @app.post('/auth/verify')
    async def auth_verify(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, '请求必须为 JSON 对象')
        body = body or {}
        email = str(body.get('email') or '').strip().lower()
        code = str(body.get('code') or '').strip()
        guest = str(body.get('guest') or '').strip()
        def verify_code():
            row = request_conn().execute(
                "SELECT rowid FROM auth_codes WHERE email=? AND code_hash=? AND used_at IS NULL "
                "AND datetime(expires_at)>datetime('now') ORDER BY rowid DESC LIMIT 1",
                (email, _hash(f'{email}:{code}'))).fetchone()
            if row is None:
                raise HTTPException(400, '验证码错误或已过期')
            request_conn().execute("UPDATE auth_codes SET used_at=datetime('now') WHERE rowid=?",
                         (row['rowid'],))
            request_conn().execute('INSERT OR IGNORE INTO users(email) VALUES(?)', (email,))
            # 合并：当前游客的记录迁到账号名下（guest 行随之清空）。
            if guest:
                session = guest_sessions.revoke(request_conn(), guest, 'merged')
                request_conn().execute('UPDATE users SET lang=? WHERE email=?',(cs_i18n.request_language(request,session['lang']),email))
                request_conn().execute(
                    "UPDATE notes SET owner_kind='user', owner_id=? WHERE owner_kind='guest' AND owner_id=?",
                    (email, session['owner_id']))
                if request_conn().execute("SELECT 1 FROM note_batches WHERE owner_kind='guest' AND owner_id=? AND active=1",(session['owner_id'],)).fetchone():
                    request_conn().execute("UPDATE note_batches SET active=0 WHERE owner_kind='user' AND owner_id=?",(email,))
                request_conn().execute(
                    "UPDATE note_batches SET owner_kind='user', owner_id=? WHERE owner_kind='guest' AND owner_id=?",
                    (email, session['owner_id']))
            token = secrets.token_urlsafe(32)
            request_conn().execute('INSERT INTO session_tokens(token_hash, email) VALUES(?,?)',
                         (_hash(token), email))
            return {'token': token, 'email': email}
        return await run_request_worker(request, verify_code)

    @app.get('/me')
    def me(request: Request):
        kind, owner_id = _identity(request, '')
        return {'kind': kind, 'email': owner_id if kind == 'user' else '', 'lang':cs_i18n.normalize_language(request.state.customer_language)}

    # ---------- 页面（nginx 经 /tool/ 反代，前缀剥掉后落到这里的根路由） ----

    _page = os.path.join(_ROOT, 'static', 'tool', 'index.html')

    @app.get('/customer-{asset}.js', include_in_schema=False)
    def language_asset(asset: str):
        if asset not in ('catalog','sources','i18n'):
            raise HTTPException(404,'Not found')
        return FileResponse(os.path.join(_ROOT,'static',f'customer-{asset}.js'))

    @app.get('/', include_in_schema=False)
    @app.get('/tool', include_in_schema=False)
    @app.get('/tool/', include_in_schema=False)
    def tool_page():
        return FileResponse(_page, headers={'Cache-Control': 'no-cache'})

    return app
