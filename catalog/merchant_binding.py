"""Short-lived bearer form. Bot tokens never enter the hub database or URLs."""
import json
import os
from pathlib import Path
import re
import secrets
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from . import merchant_onboarding as hub
from .tg import TgApi


def root():
    from . import config
    p = Path(os.environ.get('MERCHANT_RUNTIME_DIR', str(Path(config.DB_PATH).parent / 'merchants'))).resolve()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def credentials(mid):
    return root() / mid / 'credentials.json'


def write_secret(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.'+secrets.token_hex(12))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def lookup(c, key):
    m=c.execute('SELECT * FROM merchant WHERE binding_hash=? AND binding_expires>?',
                (hub.digest(key), int(time.time()))).fetchone()
    if not m: raise HTTPException(410, '链接已失效，请回商家助手回复“绑定客服”。')
    from .merchant_verification import verified
    if not verified(c,m):raise HTTPException(403,'档口身份尚未认证，请先完成认领核验。')
    return m


def identity(token):
    if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', token):
        raise HTTPException(400, 'Token 格式无效')
    if token.split(':')[0] == os.environ.get('TG_BOT_TOKEN','').split(':')[0]:
        raise HTTPException(409, '这是平台接入助手，请使用你通过 BotFather 创建的客服 bot。')
    try:
        api=TgApi(token=token); me=api._call('getMe'); webhook=api._call('getWebhookInfo')
    except Exception:
        raise HTTPException(400, '验证失败，请检查 Token 或稍后重试。') from None
    if not me.get('is_bot') or not me.get('username'): raise HTTPException(400, '无效 bot')
    if webhook.get('url'): raise HTTPException(409, '这个 bot 已接入其他 webhook 服务，请先自行解除。')
    return me


class Binding(BaseModel):
    key: str = Field(min_length=30, max_length=100)
    token: SecretStr
    confirm_bot_id: str | None = None


def register(app):
    if os.environ.get('MERCHANT_HUB_ENABLED') != '1':return
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.responses import JSONResponse
    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        if request.url.path == '/merchant/binding':
            return JSONResponse({'detail':'请求格式无效，请重新打开绑定页面。'},status_code=422)
        return await request_validation_exception_handler(request,exc)

    def secure(request):
        # TLS terminator must configure uvicorn trusted proxy addresses explicitly.
        if request.url.scheme != 'https' and not (os.environ.get('ONBOARDING_ALLOW_HTTP_TEST')=='1' and request.client.host in ('127.0.0.1','testclient','::1')):
            raise HTTPException(400, '此入口必须通过 HTTPS 访问。')
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '来源不匹配')

    from .merchant_verification import register as register_verification
    register_verification(app,secure)

    @app.post('/merchant/invitation')
    def invitation(request: Request):
        from .api import _auth
        _auth(request, app.state.token)
        path=app.state.conn.execute('PRAGMA database_list').fetchone()[2]
        c=hub.connect()
        try:return {'invitation': hub.new_invite(c, path), 'expires_in': 86400}
        finally:c.close()

    @app.post('/merchant/binding')
    def bind(body: Binding, request: Request):
        secure(request)
        c=hub.connect()
        try:
            m=lookup(c,body.key)
            if m['state']=='enabled' or m['runtime_status'] in ('starting','running'): raise HTTPException(409,'请先暂停客服再修改绑定。')
            me=identity(body.token.get_secret_value())
            if not body.confirm_bot_id:
                return {'bot_id': str(me['id']), 'username': me['username'], 'shop': json.loads(m['confirmed']).get('shop_name')}
            if body.confirm_bot_id!=str(me['id']):raise HTTPException(409,'bot 身份变化，请重新核对。')
            c.execute('BEGIN IMMEDIATE')
            m=lookup(c,body.key)
            if m['state']=='enabled' or m['runtime_status'] in ('starting','running'):raise HTTPException(409,'请先暂停客服。')
            if m['bot_id'] and m['bot_id']!=str(me['id']):raise HTTPException(409,'更换客服 bot 需要管理员迁移历史记录。')
            other=c.execute('SELECT id FROM merchant WHERE bot_id=? AND id!=?',(str(me['id']),m['id'])).fetchone()
            if other:raise HTTPException(409,'这个 bot 已绑定其他档口。')
            path=credentials(m['id'])
            existing=json.loads(path.read_text()) if path.exists() else {'api_token':secrets.token_urlsafe(32)}
            write_secret(path,{**existing,'bot_token':body.token.get_secret_value()})
            c.execute("UPDATE merchant SET bot_id=?,bot_username=?,binding_hash=NULL,binding_expires=NULL,state='ready',error='',revision=revision+1 WHERE id=?",(str(me['id']),me['username'],m['id']))
            c.commit()
            return {'bound':True,'username':me['username']}
        finally:
            c.close()

    @app.api_route('/merchant/customer/{mid}/{path:path}',methods=['GET','PATCH'])
    async def customer_proxy(mid: str, path: str, request: Request):
        # Narrow allowlist: never proxy merchant admin APIs or user-supplied hosts.
        if not re.fullmatch(r'[a-f0-9]{24}',mid):raise HTTPException(404)
        allowed=(path=='cs/list.html' and request.method=='GET') or re.fullmatch(r'cs/link/[A-Za-z0-9_-]{16,100}(?:/export\.xlsx|/note/[0-9]+(?:/photo)?)?',path)
        if not allowed:raise HTTPException(404)
        c=hub.connect()
        try:m=c.execute("SELECT port FROM merchant WHERE id=? AND state IN ('enabled','catalog_ready') AND runtime_status='running'",(mid,)).fetchone()
        finally:c.close()
        if not m:raise HTTPException(503,'客服暂未运行')
        import httpx
        from fastapi.responses import Response
        body=await request.body()
        if len(body)>20000:raise HTTPException(413)
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=30) as client:
                r=await client.request(request.method,f'http://127.0.0.1:{int(m[0])}/{path}',params=request.query_params,content=body,headers={'Content-Type':'application/json'})
        except httpx.HTTPError:raise HTTPException(503,'客服暂不可用') from None
        headers={k:v for k,v in r.headers.items() if k in ('content-type','content-disposition')}
        headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        return Response(r.content,status_code=r.status_code,headers=headers)


    @app.api_route('/merchant/manage/{mid}/{path:path}',methods=['GET','POST','PATCH','DELETE'])
    async def manage_proxy(mid: str,path: str,request: Request):
        secure(request)
        if not re.fullmatch(r'[a-f0-9]{24}',mid):raise HTTPException(404)
        key=(request.headers.get('X-Service-Token') or request.query_params.get('auth') or
             request.query_params.get('t') or request.query_params.get('token') or '')
        c=hub.connect()
        try:
            m=c.execute("SELECT * FROM merchant WHERE id=? AND manage_hash=? AND manage_expires>? AND state IN ('enabled','catalog_ready') AND runtime_status='running'",(mid,hub.digest(key),int(time.time()))).fetchone()
            # Isolated runtimes use their shop-local service credential for
            # approval links. It is already required by the sidecar API and
            # is never accepted for another merchant id.
            if not m:
                candidate=c.execute("SELECT * FROM merchant WHERE id=? AND state IN ('enabled','catalog_ready') AND runtime_status='running'",(mid,)).fetchone()
                try:
                    private=json.loads(credentials(mid).read_text())['api_token'] if candidate else ''
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    private=''
                if candidate and key and key == private:
                    m=candidate
            if m:
                from .merchant_verification import verified
                if not verified(c,m):raise HTTPException(403,'档口身份尚未认证')
        finally:c.close()
        if not m:raise HTTPException(401,'链接无效或已过期，请在接入助手回复“商品管理”。')
        # Serve the management shell and its guide assets from the hub release.
        # The isolated worker can be restarted independently and may otherwise
        # keep an older index.html, which makes the WeChat binding tab disappear
        # even though the management APIs are already on the new release.
        if request.method == 'GET' and path in ('', 'index.html'):
            from fastapi.responses import FileResponse
            static = Path(__file__).resolve().parent.parent / 'static' / 'index.html'
            return FileResponse(static, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        if request.method == 'GET' and re.fullmatch(r'guide/(?:botfather-profile|botfather-create)\.png', path):
            from fastapi.responses import FileResponse
            static = Path(__file__).resolve().parent.parent / 'static' / path
            return FileResponse(static, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        # QR login is handled by the local WeChat admin service, never by the
        # isolated catalog runtime and never with its bearer token in a page.
        if path == 'wechat-bind.html' and request.method == 'GET':
            from fastapi.responses import FileResponse
            static = Path(__file__).resolve().parent.parent / 'static' / 'merchant' / 'wechat-bind.html'
            return FileResponse(static, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        if path in ('wechat-binding/session', 'wechat-binding/start', 'wechat-binding/cancel', 'wechat-binding/finalize'):
            from . import wechat_binding
            action = {'wechat-binding/session':'status','wechat-binding/start':'start',
                      'wechat-binding/cancel':'cancel','wechat-binding/finalize':'finalize'}[path]
            if action in ('cancel','finalize'):
                try: body = await request.json()
                except ValueError: raise HTTPException(422,'请求格式无效。') from None
                sid = str(body.get('sessionId',''))
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',sid): raise HTTPException(422,'请求格式无效。')
                return wechat_binding.manage(action, sid)
            return wechat_binding.manage(action)
        # Never expose filesystem import, shop rebinding, quote jobs or hub routes.
        if path not in ('','index.html','upload','tickets','stats','categories') and not re.fullmatch(
                r'(?:products|tickets|img|ticketimg)/[^?#\\]+|categories/[^/?#]+/template\.xlsx', path):
            raise HTTPException(404)
        if '%' in path or any(part in ('.','..') for part in path.split('/')):raise HTTPException(404)
        import httpx
        from fastapi.responses import Response
        body=await request.body()
        if len(body)>12*1024*1024:raise HTTPException(413)
        private=json.loads(credentials(mid).read_text())['api_token']
        query={k:v for k,v in request.query_params.items() if k not in ('auth','t','token')}
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=60) as client:
                r=await client.request(request.method,f"http://127.0.0.1:{int(m['port'])}/{path}",params=query,content=body,headers={'X-Service-Token':private,'Content-Type':request.headers.get('content-type','application/json')})
        except httpx.HTTPError:raise HTTPException(503,'商品服务暂不可用') from None
        content=r.content
        if 'application/json' in r.headers.get('content-type',''):
            # API-produced image links are absolute paths and contain its private auth token.
            def rewrite(value):
                if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
                if isinstance(value,list):return [rewrite(v) for v in value]
                if isinstance(value,str) and value.startswith(('/img/','/ticketimg/')):
                    from urllib.parse import urlsplit, urlunsplit, urlencode
                    u=urlsplit(value)
                    return '/merchant/manage/'+mid+urlunsplit(('', '', u.path, urlencode({'token':key}), ''))
                return value
            content=json.dumps(rewrite(r.json()),ensure_ascii=False).encode()
        response_headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'}
        if r.headers.get('content-disposition'):
            response_headers['Content-Disposition']=r.headers['content-disposition']
        return Response(content,status_code=r.status_code,media_type=r.headers.get('content-type'),headers=response_headers)
