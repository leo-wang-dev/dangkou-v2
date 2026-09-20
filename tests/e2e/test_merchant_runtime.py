"""Actual supervisor and two API/bot child pairs; Telegram is a local transport fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from catalog import merchant_onboarding as hub,merchant_binding as binding


def wait_for(predicate,seconds=25):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.2)
    raise AssertionError('runtime transition timed out')


def test_two_live_workers_pause_resume(tmp_path,monkeypatch):
    class Telegram(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            botid=self.path.split('/')[1][3:].split(':')[0]
            if self.path.endswith('/getMe'):result={'id':int(botid),'username':'test_'+botid+'_bot','is_bot':True}
            else:time.sleep(.1);result=[]
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'ok':True,'result':result}).encode())
        def log_message(self,*args):pass
    telegram=ThreadingHTTPServer(('127.0.0.1',0),Telegram)
    thread=threading.Thread(target=telegram.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('MERCHANT_HUB_DB',str(tmp_path/'hub.db'))
    monkeypatch.setenv('MERCHANT_RUNTIME_DIR',str(tmp_path/'shops'))
    monkeypatch.setenv('TG_API_BASE',f'http://127.0.0.1:{telegram.server_port}')
    monkeypatch.setenv('TG_PROXY_URL','')
    c=hub.connect();ids=[]
    for owner in ('123456','234567'):
        hub.handle(c,owner,'创建新档口')
        for value in ['店铺'+owner,*(['跳过']*9)]:hub.handle(c,owner,value)
        hub.handle(c,owner,'确认配置');m=hub.account(c,owner);ids.append(m['id'])
        binding.write_secret(binding.credentials(m['id']),{'bot_token':owner+':test','api_token':'test-'+owner})
        c.execute("UPDATE merchant SET state='enabled',bot_id=?,bot_username=? WHERE id=?",(owner,'test_'+owner+'_bot',m['id']))
    c.commit()
    from scripts.create_test_shop import create as create_test_shop
    catalog_only,_=create_test_shop('345678')
    project=Path(__file__).resolve().parents[2]
    with open(tmp_path/'runtime.log','w') as log:
        worker=subprocess.Popen([sys.executable,'scripts/run_merchant_runtime.py'],cwd=project,env=os.environ.copy(),stdout=log,stderr=log)
        try:
            wait_for(lambda: c.execute("SELECT count(*) FROM merchant WHERE runtime_status='running'").fetchone()[0]==3)
            catalog=c.execute('SELECT * FROM merchant WHERE id=?',(catalog_only['id'],)).fetchone()
            assert catalog['state']=='catalog_ready' and catalog['bot_id'] is None
            import requests
            test_session=requests.Session();test_session.trust_env=False
            response=test_session.get(f"http://127.0.0.1:{catalog['port']}/cs/catalog",
                headers={'X-Service-Token':'test-345678'},timeout=3)
            # The generated credential is private; a guessed value must be rejected.
            assert response.status_code==401
            private=json.loads(binding.credentials(catalog['id']).read_text())['api_token']
            response=test_session.get(f"http://127.0.0.1:{catalog['port']}/cs/catalog",
                headers={'X-Service-Token':private},timeout=3)
            assert response.status_code==200
            assert {p['name'] for p in response.json()['products']}=={'TEST-RZ-8226','TEST-CURL-2026'}
            # Public gateway reaches the correct live tenant, never an admin route.
            from catalog import db
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
            app=FastAPI();binding.register(app);client=TestClient(app,base_url='https://testserver')
            for owner in ('123456','234567'):
                m=hub.account(c,owner);shop=db.connect(m['db_path'])
                shop.execute("INSERT INTO cs_customer(id,tg_id) VALUES('c1','999')")
                shop.execute("INSERT INTO cs_note(customer_id,photo,fields_json,status) VALUES('c1','',?,'confirmed')",(json.dumps({'型号或品名':owner}),))
                shop.execute("INSERT INTO cs_link(token,customer_id) VALUES(?,'c1')",(owner+'x'*24,));shop.commit();shop.close()
                prefix='/merchant/customer/'+m['id']
                r=client.get(prefix+'/cs/link/'+owner+'x'*24)
                assert r.status_code==200 and r.json()['notes'][0]['fields']['型号或品名']==owner
                assert client.get(prefix+'/merchant/invitation').status_code==404
                assert client.get(prefix+'/cs/list.html').status_code==200
            monkeypatch.setenv('ONBOARDING_PUBLIC_URL','https://testserver')
            links=[]
            for owner in ('123456','234567'):
                link=hub.handle(c,owner,'商品管理').split('\n')[1];c.commit();links.append(link)
                from urllib.parse import urlsplit,parse_qs
                key=parse_qs(urlsplit(link).query)['t'][0]
                prefix=urlsplit(link).path.rstrip('/')
                headers={'X-Service-Token':key}
                assert client.get(link).status_code==200
                assert client.get(prefix+'/products/razor',headers=headers).status_code==200
                result=client.post(prefix+'/products/razor/direct',headers=headers,json={'changes':{'model_no':'ITEM-'+owner,'cs_visible':'1'}})
                assert result.status_code==200
                assert client.get(prefix+'/import',headers=headers).status_code==404
                assert client.get(prefix+'/products/%252e%252e/import',headers=headers).status_code==404
                assert client.get(prefix+'/products/razor',headers={'X-Service-Token':'wrong'}).status_code==401
            assert client.get(urlsplit(links[1]).path+'products/razor',headers={'X-Service-Token':parse_qs(urlsplit(links[0]).query)['t'][0]}).status_code==401
            # Click the actual merchant catalog UI through the scoped gateway.
            import socket,uvicorn
            from playwright.sync_api import sync_playwright
            monkeypatch.setenv('ONBOARDING_ALLOW_HTTP_TEST','1')
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            gateway=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
            gt=threading.Thread(target=gateway.run,kwargs={'sockets':[sock]},daemon=True);gt.start()
            try:
                wait_for(lambda:gateway.started)
                with sync_playwright() as browser_api:
                    browser=browser_api.chromium.launch();page=browser.new_page();errors=[]
                    page.on('pageerror',lambda err:errors.append(str(err)))
                    page.goto('http://127.0.0.1:'+str(port)+urlsplit(links[0]).path+'?'+urlsplit(links[0]).query)
                    page.locator('#v-products').click()
                    page.get_by_text('ITEM-123456',exact=True).wait_for()
                    assert page.get_by_text('ITEM-234567',exact=True).count()==0
                    page.get_by_role('button',name='新增商品').click()
                    page.locator('#fg-model_no').fill('BROWSER-NEW')
                    page.locator('#fg-cs_visible').fill('1')
                    page.locator('#modalBox').get_by_role('button',name='提交').click()
                    page.get_by_text('BROWSER-NEW',exact=True).wait_for()
                    assert not errors
                    # The customer brain reads the new product through the live merchant API.
                    from catalog.csbot import CsBot
                    first_shop=hub.account(c,'123456')
                    monkeypatch.setenv('CATALOG_CS_API_URL',f"http://127.0.0.1:{first_shop['port']}")
                    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN','test-123456')
                    customer_db=db.connect(first_shop['db_path'])
                    brain=CsBot(customer_db,None,img_dir=str(tmp_path/'customer-photos'))
                    assert 'BROWSER-NEW' in brain._on_text({'id':'c1'},'看看商品')
                    customer_db.close()
                    browser.close()
            finally:gateway.should_exit=True;gt.join(timeout=10);sock.close()
            first=hub.account(c,'123456')
            assert client.get('/merchant/customer/'+first['id']+'/cs/link/'+'234567'+'x'*24).status_code==404
            hub.handle(c,'123456','暂停客服');c.commit()
            wait_for(lambda:hub.account(c,'123456')['runtime_status']=='stopped')
            assert hub.account(c,'234567')['runtime_status']=='running'
            hub.handle(c,'123456','启用客服');c.commit()
            wait_for(lambda:hub.account(c,'123456')['runtime_status']=='running')
            assert len({r[0] for r in c.execute('SELECT db_path FROM merchant')})==3
        finally:
            worker.terminate();worker.wait(timeout=30);telegram.shutdown();telegram.server_close();c.close()
