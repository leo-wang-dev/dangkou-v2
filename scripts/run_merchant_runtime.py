"""Bounded per-merchant API+customer workers. Shared DB inside a shop; isolated across shops."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config, db, merchant_onboarding as hub, merchant_policy
from catalog.merchant_binding import credentials, root

PROJECT=Path(__file__).resolve().parents[1]


def provision(c,m):
    from catalog.merchant_verification import verified
    if not verified(c,m):raise ValueError('档口身份尚未认证，请先完成认领核验')
    values=json.loads(m['confirmed'])
    with_bot=m['state']=='enabled'
    if not values.get('shop_name'):raise ValueError('档口配置未完成')
    secrets=json.loads(credentials(m['id']).read_text())
    if with_bot and (not m['bot_id'] or secrets.get('bot_token','').split(':')[0]!=m['bot_id']):
        raise ValueError('客服 bot 配置或密钥身份不匹配')
    directory=root()/m['id']; path=m['db_path'] or str(directory/'catalog.db')
    conn=db.connect(path)
    try:
        db.init_db(conn)
        current=conn.execute('SELECT tg_bot_id FROM shop_profile WHERE id=1').fetchone()[0]
        if with_bot and current and current!=m['bot_id'] and current==os.environ.get('TG_BOT_TOKEN','').split(':')[0] and m['invitation_used']:
            from scripts.migrate_merchant_bot import migrate
            migrate(c,m['id'],activation=True)
            current=m['bot_id']
        if with_bot and current and current!=m['bot_id']:
            raise ValueError('已有档口绑定其他 bot，需管理员停止原 worker 并迁移收发记录后再启用')
        if with_bot:
            conn.execute('UPDATE shop_profile SET shop_name=?,owner_tg_username=?,owner_wechat=?,tg_bot_id=?,tg_bot_username=? WHERE id=1',
                (values['shop_name'],values.get('owner_tg_username',''),values.get('owner_wechat',''),m['bot_id'],m['bot_username']))
        else:
            conn.execute('UPDATE shop_profile SET shop_name=?,owner_tg_username=?,owner_wechat=? WHERE id=1',
                (values['shop_name'],values.get('owner_tg_username',''),values.get('owner_wechat','')))
        # 开店即生成 H5 客服入口（公共 H5 平台：每店家一个稳定 /cs/chat/<token>）
        import secrets as _secrets
        if not conn.execute('SELECT chat_token FROM shop_profile WHERE id=1').fetchone()[0]:
            conn.execute('UPDATE shop_profile SET chat_token=? WHERE id=1', (_secrets.token_urlsafe(24),))
        merchant_policy.apply(conn,values,m['revision']);conn.commit()
    finally:conn.close()
    port=m['port']
    if not port:
        used={r[0] for r in c.execute('SELECT port FROM merchant WHERE port IS NOT NULL')}
        port=next(p for p in range(19000,20000) if p not in used)
    c.execute('UPDATE merchant SET db_path=?,port=? WHERE id=?',(path,port,m['id']));c.commit()
    marker=directory/'ready';marker.unlink(missing_ok=True)
    env=os.environ.copy()
    public_base=os.environ.get('ONBOARDING_PUBLIC_URL','').rstrip('/')
    env.update(CATALOG_V2_DB=path,CATALOG_V2_IMG=(config.IMG_DIR if m['invitation_used'] else str(directory/'images')),
       CATALOG_V2_SERVICE_TOKEN=secrets['api_token'],CATALOG_CS_SERVICE_TOKEN=secrets['api_token'],
       CATALOG_CS_API_URL=f'http://127.0.0.1:{port}',
       CATALOG_V2_PUBLIC_URL=public_base+'/merchant/customer/'+m['id'],
       CATALOG_V2_MANAGE_URL=public_base+'/merchant/manage/'+m['id'],
       MERCHANT_HUB_ENABLED='0',CATALOG_CS_PHOTOS=str(directory/'cs_photos'),MERCHANT_READY_FILE=str(marker))
    # This is an independent shop deployment: keep the shop-local WeChat
    # connector URL/token so handoff alerts are delivered and retried.
    env.pop('MERCHANT_REVIEW_TOKEN',None)
    env.pop('MERCHANT_REVIEW_TOKEN_EXPIRES',None)
    if with_bot:env['TG_BOT_TOKEN']=secrets['bot_token']
    else:env.pop('TG_BOT_TOKEN',None)
    return env,port,with_bot


def stop(children):
    for p in children:
        if p.poll() is None:p.terminate()
    for p in children:
        try:p.wait(timeout=8)
        except subprocess.TimeoutExpired:p.kill();p.wait()


def launch(env,port,with_bot=True):
    import requests
    api=subprocess.Popen([sys.executable,'-m','uvicorn','catalog.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=PROJECT,env=env)
    try:
        session=requests.Session();session.trust_env=False
        for _ in range(60):
            if api.poll() is not None:raise RuntimeError('档口 API 启动失败')
            try:
                r=session.get(f'http://127.0.0.1:{port}/cs/catalog',headers={'X-Service-Token':env['CATALOG_CS_SERVICE_TOKEN']},timeout=1)
                if r.status_code==200:break
            except requests.RequestException:pass
            time.sleep(.5)
        else:raise RuntimeError('档口 API 启动超时')
        if not with_bot:return [api]
        bot=subprocess.Popen([sys.executable,'scripts/run_cs_bot.py'],cwd=PROJECT,env=env)
        return [api,bot]
    except BaseException:stop([api]);raise


def main():
    c=hub.connect();running={};retry={};stopping=False
    def shutdown(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    with open(root()/'supervisor.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            while not stopping:
                rows={r['id']:r for r in c.execute('SELECT * FROM merchant')}
                for mid,(rev,state,children) in list(running.items()):
                    m=rows.get(mid)
                    if not m or m['state'] not in ('enabled','catalog_ready') or m['state']!=state or m['revision']!=rev or any(p.poll() is not None for p in children):
                        failed=any(p.poll() is not None for p in children)
                        stop(children);del running[mid]
                        c.execute("UPDATE merchant SET runtime_status='stopped' WHERE id=?",(mid,));c.commit()
                        if failed:
                            retry[mid]=time.time()+30
                            c.execute("UPDATE merchant SET runtime_status='error',error='客服进程退出，30秒后自动重试；持续失败请联系管理员。' WHERE id=?",(mid,));c.commit()
                for mid,m in rows.items():
                    if m['state'] not in ('enabled','catalog_ready') or mid in running or retry.get(mid,0)>time.time():continue
                    if len(running)>=int(os.environ.get('MERCHANT_MAX_ACTIVE','5')):continue
                    try:
                        changed=c.execute("UPDATE merchant SET runtime_status='starting' WHERE id=? AND state=? AND revision=?",(mid,m['state'],m['revision'])).rowcount
                        c.commit()
                        if not changed:continue
                        env,port,with_bot=provision(c,m)
                        latest=c.execute('SELECT state,revision FROM merchant WHERE id=?',(mid,)).fetchone()
                        if latest['state']!=m['state'] or latest['revision']!=m['revision']:
                            c.execute("UPDATE merchant SET runtime_status='stopped' WHERE id=?",(mid,));c.commit();continue
                        children=launch(env,port,with_bot)
                        running[mid]=(m['revision'],m['state'],children)
                        status='starting' if with_bot else 'running'
                        c.execute("UPDATE merchant SET runtime_status=?,error='' WHERE id=?",(status,mid))
                    except Exception as e:
                        c.rollback();retry[mid]=time.time()+30
                        # Exception text can include network credentials; only safe local ValueError text is shown.
                        error=str(e) if isinstance(e,ValueError) else '启动失败：'+type(e).__name__
                        c.execute("UPDATE merchant SET runtime_status='error',error=? WHERE id=?",(error,mid))
                    c.commit()
                for mid,(_,state,children) in running.items():
                    # Worker writes readiness only after Telegram getMe and catalog identity checks.
                    marker=root()/mid/'ready'
                    if state=='enabled' and marker.exists() and all(p.poll() is None for p in children):
                        c.execute("UPDATE merchant SET runtime_status='running' WHERE id=?",(mid,));c.commit()
                time.sleep(2)
        finally:
            for _,_,children in running.values():stop(children)
            c.execute("UPDATE merchant SET runtime_status='stopped'");c.commit();c.close()

if __name__=='__main__':main()
