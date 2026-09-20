"""QR binding for one explicitly configured, independently deployed shop."""
import fcntl
import base64
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from .merchant_binding import write_secret


def binding_path():
    value = os.environ.get('WECHAT_BINDING_FILE')
    if value:
        return Path(value)
    data_dir = Path(os.environ.get('DSH_WECHAT_DATA_DIR', str(Path.home() / 'dsh-engine' / 'data' / 'wechat')))
    return data_dir / '.merchant-binding.json'


def bound_owner_ids():
    path = binding_path()
    if path is None:
        return {x.strip() for x in os.environ.get('WECHAT_MERCHANT_OWNER_IDS', '').split(',') if x.strip()}
    try:
        owner = json.loads(path.read_text())['userId']
        return {owner} if isinstance(owner, str) and owner else set()
    except (OSError, ValueError, KeyError, TypeError):
        return set()  # Never restore revoked owners on corrupt/missing binding state.


class SessionBody(BaseModel):
    sessionId: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


class BindingService:
    def __init__(self):
        self.path = binding_path()
        self.url = os.environ.get('WECHAT_BINDING_ADMIN_URL', 'http://127.0.0.1:17605').rstrip('/')
        self.token_path = os.environ.get('WECHAT_BINDING_ADMIN_TOKEN_FILE', str(self.path.parent / '.admin-token'))
        u = urlsplit(self.url)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', '::1'):
            raise HTTPException(503, '此档口尚未配置微信扫码服务，请联系开通人员。')
        self.operation = self.path.with_suffix('.session.json')

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, '绑定正在处理中，请稍后重试。') from None
            try:
                try:
                    token = Path(self.token_path).read_text().strip()
                except OSError:
                    raise HTTPException(503, '微信扫码服务暂不可用。') from None
                if not token:
                    raise HTTPException(503, '微信扫码服务暂不可用。')
                with httpx.Client(base_url=self.url, headers={'Authorization': 'Bearer ' + token},
                                  trust_env=False, timeout=25) as self.client:
                    yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def call(self, method, path, body=None, missing_ok=False):
        try:
            r = self.client.request(method, path, json=body)
            if missing_ok and r.status_code == 404:
                return {}
            if not r.is_success:
                raise HTTPException(409 if r.status_code in (400, 409) else 503,
                                    '微信绑定尚未完成，请检查手机确认状态后重试。')
            data = r.json()
            if not data.get('ok'):
                raise ValueError('unsuccessful response')
            return data
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, '微信扫码服务连接失败，请稍后重试。') from None

    @staticmethod
    def read(path):
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise HTTPException(503, '绑定状态无法读取，请联系开通人员。') from None

    def status(self):
        session = self.call('GET', '/v1/login/session')['session']
        op = self.read(self.operation)
        # Only display sessions created from this shop entry.
        if session.get('sessionId') != op.get('sessionId'):
            session = {'state': 'idle', 'sessionId': None}
        if op.get('done') and session.get('sessionId') == op.get('sessionId'):
            session = {'state': 'finalized', 'sessionId': op['sessionId']}
        # The iLink service often returns a short-lived image URL on a domain
        # unavailable to the merchant's browser/VPN. Fetch it server-side and
        # send only the image bytes to the H5 page.
        if session.get('qrUrl') and not session.get('qrPngBase64'):
            try:
                image = httpx.get(session['qrUrl'], trust_env=False, timeout=15)
                if image.is_success and image.content and len(image.content) <= 2 * 1024 * 1024:
                    looks_image = image.content.startswith((b'\x89PNG', b'\xff\xd8', b'GIF8', b'RIFF'))
                    if looks_image:
                        session['qrPngBase64'] = base64.b64encode(image.content).decode('ascii')
                    else:
                        # liteapp.weixin.qq.com returns a web page whose
                        # qrcode URL is the actual scan target. Encode that
                        # target locally into a real PNG for all browsers.
                        import qrcode
                        image = qrcode.make(session['qrUrl'])
                        buf = io.BytesIO()
                        image.save(buf, format='PNG')
                        session['qrPngBase64'] = base64.b64encode(buf.getvalue()).decode('ascii')
            except httpx.HTTPError:
                pass
        allowed = ('sessionId', 'state', 'qrPngBase64', 'qrUrl', 'expiresAt')
        return {'session': {k: session.get(k) for k in allowed},
                'bound': bool(self.read(self.path).get('userId'))}

    def start(self):
        op = self.read(self.operation)
        if op.get('account') and not op.get('done'):
            raise HTTPException(409, '上次换绑仍在收尾，请点击重试完成绑定。')
        accounts = self.call('GET', '/v1/accounts')['accounts']
        current = self.read(self.path)
        old = current.get('normalizedAccountId')
        if not old and len(accounts) == 1:
            old = accounts[0].get('normalizedAccountId')
        if len(accounts) > 1 or any(a.get('normalizedAccountId') != old for a in accounts):
            raise HTTPException(409, '微信服务账号与本档口绑定不一致，请联系开通人员核对。')
        active = self.call('GET', '/v1/login/session')['session']
        if active.get('state') not in ('idle', 'cancelled', 'expired', 'failed', 'finalized'):
            if active.get('sessionId') != op.get('sessionId'):
                # A confirmed session may be left by a previous deployment
                # before the merchant page was introduced. It is safe to
                # close that one and start a fresh, page-owned QR session.
                if active.get('state') == 'confirmed' and not op.get('sessionId'):
                    self.call('POST', '/v1/login/cancel', {'sessionId': active['sessionId']})
                else:
                    raise HTTPException(409, '微信服务已有其他扫码任务，请稍后重试。')
            else:
                return self.status()
        session = self.call('POST', '/v1/login/start')['session']
        write_secret(self.operation, {'sessionId': session['sessionId'], 'old': old})
        return self.status()

    def cancel(self, sid):
        op = self.read(self.operation)
        if sid != op.get('sessionId'):
            raise HTTPException(409, '二维码已更新，请刷新页面。')
        state = self.call('GET', '/v1/login/session')['session']
        if op.get('account') or state.get('state') not in ('qr_ready', 'scanned', 'needs_verification', 'expired', 'failed'):
            raise HTTPException(409, '当前扫码已确认，请先完成绑定。')
        self.call('POST', '/v1/login/cancel', {'sessionId': sid})
        return {'cancelled': True}

    def finalize(self, sid):
        op = self.read(self.operation)
        if sid != op.get('sessionId'):
            raise HTTPException(409, '二维码已更新，请刷新页面。')
        if op.get('done'):
            return {'bound': True, 'replaced': bool(op.get('old'))}
        if not op.get('account'):
            session = self.call('GET', '/v1/login/session')['session']
            if session.get('sessionId') != sid or session.get('state') not in ('confirmed', 'finalized'):
                raise HTTPException(409, '请先用微信扫码并在手机确认。')
            # A process may restart after engine finalize but before our journal write.
            if session['state'] == 'finalized':
                account = next((a for a in self.call('GET', '/v1/accounts')['accounts']
                                if a.get('normalizedAccountId') == session.get('normalizedAccountId')), {})
            else:
                account = self.call('POST', '/v1/login/finalize', {'sessionId': sid}).get('account', {})
            if not account.get('userId') or not account.get('normalizedAccountId'):
                raise HTTPException(503, '微信未返回完整身份，尚未替换原管理账号，请联系开通人员。')
            op['account'] = {k: account.get(k) for k in ('userId', 'normalizedAccountId', 'accountId')}
            write_secret(self.operation, op)
        # Shared authority changes atomically. Engine inbound/outbound and notifications
        # read this file on each operation; the old owner loses access immediately.
        write_secret(self.path, op['account'])
        if op.get('old') and op['old'] != op['account']['normalizedAccountId']:
            self.call('DELETE', '/v1/accounts/' + quote(op['old'], safe=''), missing_ok=True)
        op['done'] = True
        write_secret(self.operation, op)
        return {'bound': True, 'replaced': bool(op.get('old'))}


def manage(action, *args):
    """Run a management action after the caller has authenticated the shop link."""
    service = BindingService()
    with service.locked():
        result = getattr(service, action)(*args)
    return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


def register(app):
    if os.environ.get('WECHAT_CUSTOMER_BOT_ENABLED') != '1':
        return
    from .api import _auth

    def run(request, action, *args):
        _auth(request, app.state.token)
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '来源不匹配。')
        service = BindingService()
        with service.locked():
            result = getattr(service, action)(*args)
        return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.get('/wechat-binding/session')
    def status(request: Request):
        return run(request, 'status')

    @app.post('/wechat-binding/start')
    def start(request: Request):
        return run(request, 'start')

    @app.post('/wechat-binding/cancel')
    def cancel(body: SessionBody, request: Request):
        return run(request, 'cancel', body.sessionId)

    @app.post('/wechat-binding/finalize')
    def finalize(body: SessionBody, request: Request):
        return run(request, 'finalize', body.sessionId)

    return manage
