"""Optional APP-to-merchant login. No product, ticket, or queue business logic."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

COOKIE = '__Host-dangkou_app_session'
TICKET_TTL = 60
SESSION_TTL = 8 * 60 * 60
# Exact methods and paths, deliberately excluding import, readiness, linkage,
# customer APIs and merchant provisioning. This is not a generic service key.
MANAGEMENT = [
    ('GET|POST', r'/categories'),
    ('PATCH', r'/categories/[^/]+'),
    ('GET|PUT', r'/categories/[^/]+/quote-map'),
    ('PATCH', r'/categories/[^/]+/visibility'),
    ('GET', r'/categories/[^/]+/template\.xlsx'),
    ('GET', r'/stats(?:/supplier)?'),
    ('GET', r'/img/.+'),
    ('POST', r'/upload'),
    ('GET', r'/tickets(?:/\d+)?'),
    ('GET', r'/ticketimg/\d+/.+'),
    ('PATCH', r'/tickets/\d+/draft'),
    ('POST', r'/tickets/\d+/decision'),
    ('GET|POST', r'/products/[^/]+'),
    ('PATCH|DELETE', r'/products/[^/]+/[^/]+'),
    ('POST', r'/products/[^/]+/direct'),
    ('PATCH|DELETE', r'/products/[^/]+/[^/]+/direct'),
    ('POST', r'/search|/quote'),
    ('GET', r'/quotes/[^/]+'),
    ('GET|PATCH', r'/shop'),
    ('GET|POST', r'/cs/redline'),
    ('GET', r'/cs/photo'),
    ('POST', r'/cs/chat-token'),
    ('GET', r'/wechat-binding/session'),
    ('POST', r'/wechat-binding/(?:start|cancel|finalize)'),
]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS app_entry_tickets (
        code_hash TEXT PRIMARY KEY, authority TEXT NOT NULL,
        actor_id TEXT NOT NULL, upstream_session_id TEXT NOT NULL,
        request_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
        consumed_at INTEGER, revoked_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS app_entry_tickets_exp ON app_entry_tickets(expires_at);
    CREATE INDEX IF NOT EXISTS app_entry_tickets_upstream
        ON app_entry_tickets(authority, upstream_session_id);
    CREATE TABLE IF NOT EXISTS app_entry_sessions (
        session_hash TEXT PRIMARY KEY, authority TEXT NOT NULL,
        actor_id TEXT NOT NULL, upstream_session_id TEXT NOT NULL,
        expires_at INTEGER NOT NULL, revoked_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS app_entry_sessions_exp ON app_entry_sessions(expires_at);
    CREATE INDEX IF NOT EXISTS app_entry_sessions_upstream
        ON app_entry_sessions(authority, upstream_session_id);
    ''')


def settings(service_token=''):
    enabled = os.environ.get('CATALOG_APP_ACCESS_ENABLED') == '1'
    secret = os.environ.get('CATALOG_APP_ENTRY_SECRET', '')
    base = os.environ.get('CATALOG_APP_PUBLIC_BASE_URL', '').rstrip('/')
    shop = os.environ.get('CATALOG_APP_SHOP_ID', '').strip()
    try:
        url = urlsplit(base)
        valid = (url.scheme == 'https' and url.hostname and not url.username
                 and not url.password and not url.path and not url.query
                 and not url.fragment and (url.port is None or 1 <= url.port <= 65535)
                 and not re.search(r'[\s\\]', base))
    except ValueError:
        valid = False
    if not enabled or len(secret) < 32 or not valid or not shop or secret == service_token:
        raise HTTPException(503, 'APP 登录接入未启用或配置不完整')
    host = url.hostname.encode('idna').decode('ascii')
    if ':' in host:
        host = '[' + host + ']'
    base = 'https://' + host + (':' + str(url.port) if url.port not in (None, 443) else '')
    authority = digest('\0'.join([secret, base, shop]))
    return secret, base, shop, authority


def require_origin(request, base):
    if (request.headers.get('origin') != base
            or request.headers.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'none')):
        raise HTTPException(403, '请求来源不匹配')


def require_backend(request, secret):
    if request.headers.get('origin') or request.headers.get('sec-fetch-site'):
        raise HTTPException(403, '仅限可信后台调用')
    supplied = request.headers.get('authorization', '')
    if not hmac.compare_digest(supplied.encode(), ('Bearer ' + secret).encode()):
        raise HTTPException(401, '接入凭证无效')


def session(request, conn):
    try:
        secret, base, shop, authority = settings(request.app.state.token)
    except HTTPException:
        raise HTTPException(401, '请从 APP 重新进入档口')
    raw = request.cookies.get(COOKIE, '')
    if not raw or len(raw) > 128:
        raise HTTPException(401, '请从 APP 重新进入档口')
    hashed = digest(raw)
    row = conn.execute('''SELECT * FROM app_entry_sessions WHERE session_hash=?
        AND authority=? AND expires_at>? AND revoked_at IS NULL''',
        (hashed, authority, int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401, '请从 APP 重新进入档口')
    csrf = hmac.new(secret.encode(), ('csrf:' + hashed).encode(), hashlib.sha256).hexdigest()
    return row, csrf, base, shop


def check_csrf(request, csrf, base):
    require_origin(request, base)
    if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), csrf.encode()):
        raise HTTPException(403, '请求校验失败，请重新进入档口')


class MintIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    actorId: str = Field(min_length=1, max_length=128)
    upstreamSessionId: str = Field(min_length=1, max_length=128)
    requestId: str = Field(min_length=1, max_length=128)


class ExchangeIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=20, max_length=128)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    upstreamSessionId: str = Field(min_length=1, max_length=128)


def register(app, request_conn):
    @app.middleware('http')
    async def app_authentication(request, call_next):
        path = request.url.path
        marked = request.headers.get('x-catalog-app') == '1'
        cookie = request.cookies.get(COOKIE)
        methods = [methods for methods, pattern in MANAGEMENT if re.fullmatch(pattern, path)]
        # Explicit legacy credentials continue to work in a browser that once
        # used APP login. Marked APP requests can NEVER fall back to these.
        legacy = request.headers.get('x-service-token') or request.query_params.get('token') or request.query_params.get('auth')
        valid_legacy = (not marked and legacy and app.state.token
                        and hmac.compare_digest(legacy.encode(), app.state.token.encode()))
        try:
            if not path.startswith('/app-entry/') and (marked or (cookie and methods)) and not valid_legacy:
                if not any(request.method in values.split('|') for values in methods):
                    raise HTTPException(403, '该接口不接受 APP 网页会话')
                row, csrf, base, _ = session(request, request_conn())
                if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                    check_csrf(request, csrf, base)
                request.state.app_management = True
                request.state.app_actor_id = row['actor_id']
            response = await call_next(request)
        except HTTPException as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code)
        if path.startswith('/app-entry/') or marked or cookie:
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/app-entry/')
    def entry():
        settings(app.state.token)
        return FileResponse(Path(__file__).resolve().parent.parent / 'static/app-entry/index.html',
                            headers={'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    @app.post('/app-entry/tickets')
    def tickets(body: MintIn, request: Request):
        secret, base, shop, authority = settings(request.app.state.token)
        require_backend(request, secret)
        if request.scope.get('root_path'):
            raise HTTPException(503, 'APP 登录目前仅支持独立域名根路径部署')
        now = int(time.time())
        conn = request_conn()
        code = secrets.token_urlsafe(32)
        with conn:
            conn.execute('DELETE FROM app_entry_tickets WHERE expires_at<=?', (now,))
            conn.execute('DELETE FROM app_entry_sessions WHERE expires_at<=?', (now,))
            conn.execute('''INSERT INTO app_entry_tickets
                (code_hash,authority,actor_id,upstream_session_id,request_id,expires_at)
                VALUES(?,?,?,?,?,?)''',
                (digest(code), authority, body.actorId, body.upstreamSessionId, body.requestId, now + TICKET_TTL))
        return {'loginUrl': base + '/app-entry/#code=' + code,
                'expiresIn': TICKET_TTL, 'shopId': shop}

    @app.post('/app-entry/exchange')
    def exchange(body: ExchangeIn, request: Request):
        _, base, shop, authority = settings(app.state.token)
        require_origin(request, base)
        conn = request_conn()
        now = int(time.time())
        raw = secrets.token_urlsafe(32)
        # Compare-and-consume under a writer lock; concurrent exchange cannot
        # produce two sessions, including across separate server processes.
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('''SELECT * FROM app_entry_tickets WHERE code_hash=?
                AND authority=? AND expires_at>? AND consumed_at IS NULL AND revoked_at IS NULL''',
                (digest(body.code), authority, now)).fetchone()
            if row is None:
                raise HTTPException(401, '登录链接已失效，请从 APP 重新进入')
            conn.execute('UPDATE app_entry_tickets SET consumed_at=? WHERE code_hash=?', (now, digest(body.code)))
            # A WebView replaces its previous session, rather than leaking it.
            previous = request.cookies.get(COOKIE, '')
            if previous:
                conn.execute('UPDATE app_entry_sessions SET revoked_at=? WHERE session_hash=?', (now, digest(previous)))
            conn.execute('''INSERT INTO app_entry_sessions
                (session_hash,authority,actor_id,upstream_session_id,expires_at) VALUES(?,?,?,?,?)''',
                (digest(raw), authority, row['actor_id'], row['upstream_session_id'], now + SESSION_TTL))
        response = JSONResponse({'redirectUrl': '/?app_session=1', 'shopId': shop})
        response.set_cookie(COOKIE, raw, max_age=SESSION_TTL, httponly=True, secure=True, samesite='lax', path='/')
        return response

    @app.get('/app-entry/session')
    def current(request: Request):
        row, csrf, _, shop = session(request, request_conn())
        return {'actorId': row['actor_id'], 'shopId': shop,
                'expiresAt': row['expires_at'], 'csrfToken': csrf}

    @app.post('/app-entry/logout')
    def logout(request: Request):
        row, csrf, base, _ = session(request, request_conn())
        check_csrf(request, csrf, base)
        conn = request_conn()
        with conn:
            conn.execute('UPDATE app_entry_sessions SET revoked_at=? WHERE session_hash=?',
                         (int(time.time()), row['session_hash']))
        response = JSONResponse({'success': True})
        response.delete_cookie(COOKIE, path='/', httponly=True, secure=True, samesite='lax')
        return response

    @app.post('/app-entry/revoke')
    def revoke(body: RevokeIn, request: Request):
        secret, _, _, authority = settings(app.state.token)
        require_backend(request, secret)
        conn = request_conn()
        with conn:
            for table in ('app_entry_tickets', 'app_entry_sessions'):
                conn.execute(f'UPDATE {table} SET revoked_at=? WHERE authority=? AND upstream_session_id=?',
                             (int(time.time()), authority, body.upstreamSessionId))
        return {'success': True}
