"""Actual registered gateway and two file-backed tenant APIs; no remote providers."""
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles
from PIL import Image

from catalog import db, cs_i18n, llm, merchant_binding as binding, merchant_onboarding as hub, merchant_policy
from catalog.api import register_routes
from catalog.storage import LocalStorage
from tests.test_merchant_onboarding import finish


@pytest.fixture
def tenant_chat(tmp_path, monkeypatch):
    monkeypatch.setenv('MERCHANT_HUB_DB', str(tmp_path/'hub.db'))
    monkeypatch.setenv('MERCHANT_RUNTIME_DIR', str(tmp_path/'shops'))
    monkeypatch.setenv('CATALOG_CS_PHOTOS', str(tmp_path/'photos'))
    monkeypatch.delenv('CATALOG_CS_API_URL', raising=False)
    monkeypatch.setenv('ONBOARDING_ALLOW_HTTP_TEST','1')
    monkeypatch.setenv('ONBOARDING_PUBLIC_URL','https://example.test')
    monkeypatch.setattr(llm,'chat_vision',lambda *a,**kw:json.dumps([{'型号或品名':'MODEL-1'}]))
    monkeypatch.setattr(llm,'chat_text',lambda *a,**kw:'{"actions":[]}')
    from catalog import agent, notify
    def forbidden(*a,**kw):raise AssertionError('offline tenant test forbids provider/child/mail')
    monkeypatch.setattr(agent,'_run_container',forbidden)
    monkeypatch.setattr(notify,'push_file',forbidden)
    apps=[]
    def make_app(name):
        conn=db.connect(str(tmp_path/(name+'.db')));db.init_db(conn)
        merchant_policy.apply(conn, {'wechat_managed':True},1)
        conn.execute("UPDATE shop_profile SET owner_wechat='boss'");conn.commit()
        app=FastAPI();app.state.conn=conn;app.state.token='private-'+name
        app.state.storage=LocalStorage(str(tmp_path/(name+'-images')));app.state.callback=None
        register_routes(app)
        app.mount('/',StaticFiles(directory=str(Path(__file__).resolve().parents[1]/'static'),html=True))
        apps.append(app);return app
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','0')
    tenants=[make_app('tenant0'),make_app('tenant1')]
    c=hub.connect();mids=[]
    for index in range(2):
        owner=str(100+index);finish(c,owner);mid=hub.account(c,owner)['id'];mids.append(mid)
        binding.write_secret(binding.credentials(mid),{'api_token':'private-tenant'+str(index)})
        c.execute("UPDATE merchant SET state='catalog_ready',runtime_status='running',port=?,manage_hash=?,manage_expires=? WHERE id=?",(19002+index,hub.digest('manage-'+str(index)),int(time.time())+600,mid))
    c.commit()
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
    gateway=make_app('fixed')
    # Only server-selected loopback ports reach a tenant, via actual ASGI routes.
    calls=[]
    class TenantTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            assert request.url.host=='127.0.0.1' and request.url.port in (19002,19003)
            calls.append((request.url.port,request.method,request.url.path,dict(request.headers)))
            return await httpx.ASGITransport(app=tenants[request.url.port-19002]).handle_async_request(request)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(**{**kwargs,'transport':TenantTransport()}))
    with TestClient(gateway,base_url='https://example.test') as client:
        yield SimpleNamespace(app=gateway,client=client,tenants=tenants,mids=mids,calls=calls,tmp_path=tmp_path)
    c.close()
    for app in apps:app.state.conn.close()


def issue(t,index):
    prefix='/merchant/customer/'+t.mids[index]
    managed='/merchant/manage/'+t.mids[index]
    result=t.client.post(managed+'/cs/chat-token',headers={'X-Service-Token':'manage-'+str(index)})
    assert result.status_code==200,result.text
    token=result.json()['chat_token'];chat=prefix+'/cs/chat/'+token
    assert t.client.get(chat).status_code==200
    result=t.client.post(chat+'/session');assert result.status_code==200,result.text
    return prefix,chat,result.json()['visitor']


def picture():
    data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,'JPEG');return data.getvalue()


def test_dynamic_chat_chain_and_tenant_capabilities(tenant_chat):
    t=tenant_chat;contexts=[issue(t,i) for i in range(2)]
    for index,(prefix,chat,visitor) in enumerate(contexts):
        headers={'X-Customer-Language':'ar'}
        assert t.client.post(chat+'/lang',json={'visitor':visitor,'lang':'ar'}).json()['lang']=='ar'
        state=t.client.get(chat+'/session',params={'visitor':visitor});assert state.json()['lang']=='ar'
        response=t.client.post(chat+'/photo',headers=headers,data={'visitor':visitor},files={'file':('a.jpg',picture(),'image/jpeg')})
        assert response.status_code==200 and response.json()['status']=='intent_required'
        assert t.client.post(chat+'/pending-photo/discard',json={'visitor':visitor}).json()['discarded']
        assert t.client.post(chat+'/mode',json={'visitor':visitor,'mode':'notes'}).status_code==200
        response=t.client.post(chat+'/photo',headers=headers,data={'visitor':visitor},files={'file':('a.jpg',picture(),'image/jpeg')})
        assert response.status_code==200,response.text
        assert t.client.post(chat+'/batches/confirm',json={'visitor':visitor,'fields':{'档口名称':'TENANT-'+str(index)}}).status_code==200
        assert t.client.post(chat+'/message',headers=headers,json={'visitor':visitor,'action':'confirm'}).status_code==200
        link=t.client.get(chat+'/list-token',params={'visitor':visitor}).json()['token'];assert link
        response=t.client.get(prefix+'/cs/link/'+link,headers=headers);assert response.status_code==200
        note=response.json()['notes'][0]
        assert t.client.get(prefix+'/cs/link/'+link+'/note/'+str(note['id'])+'/photo').status_code==200
        assert t.client.patch(prefix+'/cs/link/'+link+'/note/'+str(note['id']),json={'field':'型号或品名','value':'TENANT-'+str(index)}).status_code==200
        exported=t.client.get(prefix+'/cs/link/'+link+'/export.xlsx',headers=headers);assert exported.status_code==200
        import openpyxl
        assert openpyxl.load_workbook(io.BytesIO(exported.content)).active.sheet_view.rightToLeft
        other_prefix,other_chat,other_visitor=contexts[1-index]
        assert t.client.get(other_prefix+'/cs/chat/'+chat.split('/')[-1]).status_code==404
        assert t.client.get(other_prefix+'/cs/link/'+link).status_code==404
        assert t.client.get(other_chat+'/session',params={'visitor':visitor}).status_code in (401,410)
        for path in ('cs/chat-token','cs/catalog','categories','merchant/invitation','cs/link/'+link+'/note/1'):
            assert t.client.get(prefix+'/'+path).status_code==404
        assert t.client.post(prefix+'/cs/link/'+link).status_code==404
        assert t.client.post('/merchant/manage/'+t.mids[1-index]+'/cs/chat-token',headers={'X-Service-Token':'manage-'+str(index)}).status_code==401
        # Host/port query values cannot replace the server-owned route target.
        assert t.client.get(chat,params={'host':'attacker.invalid','port':9999}).status_code==200
        assert t.calls[-1][0]==19002+index
        assert t.client.post(chat+'/session/end',json={'visitor':visitor}).status_code==200
    fixed=t.client.post('/cs/chat-token',headers={'X-Service-Token':'private-fixed'}).json()['chat_token']
    assert t.client.post('/cs/chat/'+fixed+'/session').status_code==200


@pytest.mark.parametrize('kind',['files','fields','bytes'])
@pytest.mark.parametrize('language',['ar','fr'])
def test_gateway_multipart_limits(tenant_chat,monkeypatch,kind,language):
    t=tenant_chat;_,chat,visitor=issue(t,0)
    parts=[('visitor',(None,visitor)),('file',('a.jpg',picture(),'image/jpeg'))]
    if kind=='files':parts.append(('file',('b.jpg',picture(),'image/jpeg')))
    elif kind=='fields':parts += [('f'+str(i),(None,'x')) for i in range(8)]
    else:
        monkeypatch.setenv('CATALOG_UPLOAD_MAX_BYTES','64')
        parts=[('file',('a.jpg',b'x'*70000,'image/jpeg'))]
    response=t.client.post(chat+'/photo',files=parts,headers={'X-Customer-Language':language})
    assert response.status_code==(413 if kind=='bytes' else 400),response.text
    data=response.json()
    if kind=='bytes':assert data['detail']==cs_i18n.t('uploadError',language)
    else:assert data=={'detail':cs_i18n.t('uploadTooManyFiles' if kind=='files' else 'uploadTooManyFields',language),'code':'upload_too_many_'+kind}
