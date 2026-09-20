"""Single provisioned shop: WeChat-owned configuration, independent Telegram runtime."""
import json
import asyncio
import os
from pathlib import Path
import re
import time
from fastapi import HTTPException, Request
from . import config, shop_link, merchant_policy
from .merchant_binding import write_secret
from .wechat_binding import bound_owner_ids
from .tg import TgApi


def state_dir():
    return Path(os.environ.get('WECHAT_CUSTOMER_STATE', str(Path(config.DB_PATH).parent/'customer-runtime')))


def secret_path():
    return state_dir()/'credentials.json'


def identity(token):
    if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}',token):
        raise HTTPException(400,'Token格式无效，请重新从BotFather复制。')
    try:
        api=TgApi(token=token)
        results=[]
        for method in ('getMe','getWebhookInfo'):
            response=api.s.post(f'{api.base}/bot{token}/{method}',json={},timeout=(3,5))
            response.raise_for_status(); result=response.json()
            if not result.get('ok'):raise ValueError('invalid Telegram response')
            results.append(result['result'])
        me,webhook=results
    except Exception:
        raise HTTPException(400,'Token验证失败，请检查Token或稍后重试。') from None
    if not me.get('is_bot') or not re.fullmatch(r'[A-Za-z0-9_]+',me.get('username','')):
        raise HTTPException(400,'无效Bot身份。')
    if webhook.get('url'):
        raise HTTPException(409,'该Bot已接入其他服务，请先解除原webhook。')
    return me


def activate(conn):
    """Enable local authority; do not turn untouched legacy seed into merchant consent."""
    from . import cs
    seed=conn.execute("SELECT text_raw FROM cs_redline WHERE product_id='' ").fetchone()
    approved=conn.execute(
        "SELECT 1 FROM approval_ticket WHERE ticket_type='redline' AND status='approved' "
        "AND COALESCE(json_extract(payload,'$.product_id'),'')='' "
        "AND json_extract(payload,'$.text_raw')=? LIMIT 1", (cs.DEFAULT_STORE_REDLINE,)).fetchone()
    if seed and seed[0]==cs.DEFAULT_STORE_REDLINE and not approved:
        conn.execute("UPDATE cs_redline SET text_raw='',text_summary='' WHERE product_id='' ")
    merchant_policy.apply(conn,{'wechat_managed':True},1)


def register(app):
    if os.environ.get('WECHAT_CUSTOMER_BOT_ENABLED')!='1':return
    # This deployment is merchant-managed as soon as its WeChat management
    # entry is enabled.  Waiting for the Telegram token would expose legacy
    # seed rules while the merchant is still configuring the shop.
    activate(app.state.conn)
    app.state.conn.commit()
    from .api import _auth

    @app.get('/wechat/customer-bot/status')
    def status(request:Request):
        _auth(request,app.state.token)
        p=shop_link.profile(app.state.conn)
        runtime={'runtime_status':'pending' if p['tg_bot_id'] else 'unbound'}
        try:
            saved=json.loads((state_dir()/'status.json').read_text())
            if time.time()-saved.get('updated_at',0)<20:
                runtime={'runtime_status':saved.get('runtime_status','pending')}
        except (OSError,ValueError):pass
        return {'enabled':True,'bot_username':p['tg_bot_username'],
                'bot_url':'https://t.me/'+p['tg_bot_username'] if p['tg_bot_username'] else '',**runtime}

    @app.post('/wechat/customer-bot/bind')
    async def bind(request:Request):
        _auth(request,app.state.token)
        if len(await request.body())>4096:raise HTTPException(400,'请求过大。')
        try:body=await request.json()
        except ValueError:raise HTTPException(400,'请求格式无效。') from None
        if not isinstance(body,dict) or not isinstance(body.get('token'),str) or not isinstance(body.get('owner_id'),str):
            raise HTTPException(400,'请求格式无效。')
        # Keep the explicit allowlist for pre-provisioned deployments, while
        # also accepting the owner that just completed the QR binding flow.
        # This lets a new merchant self-serve after scanning the management
        # page; no operator-side env edit or service restart is required.
        owners={x.strip() for x in os.environ.get('WECHAT_MERCHANT_OWNER_IDS','').split(',') if x.strip()}
        owners.update(bound_owner_ids())
        if body['owner_id'] not in owners:raise HTTPException(403,'此微信身份未获该档口管理授权。')
        token=body['token'].strip()
        if token.split(':')[0] in {x.strip() for x in os.environ.get('WECHAT_RESERVED_TG_BOT_IDS','').split(',') if x.strip()}:
            raise HTTPException(409,'该Bot已用于其他运行服务，请创建独立客户Bot。')
        try:me=await asyncio.wait_for(asyncio.to_thread(identity,token),timeout=16)
        except TimeoutError:raise HTTPException(504,'验证超时，请稍后重试。') from None
        conn=app.state.conn
        # Lock serializes concurrent bind attempts after network verification.
        previous=json.loads(secret_path().read_text()) if secret_path().exists() else None
        conn.execute('BEGIN IMMEDIATE')
        wrote_secret=False
        try:
            p=shop_link.profile(conn)
            if not p['shop_name']:raise HTTPException(409,'请先完成人工档口开通和名称登记。')
            if not (p['owner_wechat'].strip() or p['owner_tg_username'].strip()):
                raise HTTPException(409,'请先在微信中补充老板微信号或Telegram联系方式，并批准资料工单后再开通客户Bot。')
            if p['tg_bot_id'] and p['tg_bot_id']!=str(me['id']):
                raise HTTPException(409,'本档口已绑定其他Bot，更换需管理员迁移历史消息。')
            conn.execute('UPDATE shop_profile SET tg_bot_id=?,tg_bot_username=? WHERE id=1',(str(me['id']),me['username']))
            activate(conn)
            write_secret(secret_path(),{'bot_id':str(me['id']),'bot_token':token})
            wrote_secret=True
            conn.commit()
        except Exception:
            conn.rollback()
            if wrote_secret:
                if previous is None:secret_path().unlink(missing_ok=True)
                else:write_secret(secret_path(),previous)
            raise
        return {'bot_username':me['username'],'bot_url':'https://t.me/'+me['username'], 'runtime_status':'pending',
                'message':'绑定已保存，正在启动客户Bot；请在商品管理查看运行状态。'}
