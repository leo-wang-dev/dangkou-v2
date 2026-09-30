"""APP login must reuse merchant operations without broadening service authentication."""
import concurrent.futures
import io
from urllib.parse import urlsplit, parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from catalog import db
from catalog.api import register_routes
from catalog.storage import LocalStorage
from tests.conftest import seed_products

ORIGIN = 'https://shop.example:8443'
SECRET = 'test-app-backend-secret-' + 'x' * 32
BACKEND = {'Authorization': 'Bearer ' + SECRET}
BROWSER = {'Origin': ORIGIN, 'X-Catalog-App': '1'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_APP_ACCESS_ENABLED', '1')
    monkeypatch.setenv('CATALOG_APP_ENTRY_SECRET', SECRET)
    monkeypatch.setenv('CATALOG_APP_PUBLIC_BASE_URL', ORIGIN)
    monkeypatch.setenv('CATALOG_APP_SHOP_ID', 'shop-one')
    app = FastAPI()
    app.state.conn = db.connect(str(tmp_path / 'shop.db'))
    db.init_db(app.state.conn)
    app.state.token = 'legacy-service-secret'
    app.state.storage = LocalStorage(str(tmp_path / 'images'))
    app.state.callback = None
    register_routes(app)
    with TestClient(app, base_url=ORIGIN) as c:
        yield c
    app.state.conn.close()


def mint(client, **patch):
    return client.post('/app-entry/tickets', headers=BACKEND, json={
        'actorId': 'user-1', 'upstreamSessionId': 'app-session-1',
        'requestId': 'request-1', **patch})


def code_of(response):
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['shopId'] == 'shop-one'
    assert data['expiresIn'] == 60
    assert data['loginUrl'].startswith(ORIGIN + '/app-entry/#code=')
    return parse_qs(urlsplit(data['loginUrl']).fragment)['code'][0]


def login(client, **patch):
    code = code_of(mint(client, **patch))
    response = client.post('/app-entry/exchange', headers=BROWSER, json={'code': code})
    assert response.status_code == 200, response.text
    return response


def app_headers(client):
    r = client.get('/app-entry/session', headers=BROWSER)
    assert r.status_code == 200, r.text
    return {**BROWSER, 'X-CSRF-Token': r.json()['csrfToken']}


def test_ticket_exchange_cookie_hash_only_and_replay(client):
    code = code_of(mint(client))
    response = client.post('/app-entry/exchange', headers=BROWSER, json={'code': code})
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'Path=/' in cookie
    assert 'samesite=lax' in cookie.lower() and 'Domain=' not in cookie
    assert response.json()['redirectUrl'] == '/?app_session=1'
    dump = '\n'.join(client.app.state.conn.iterdump())
    assert code not in dump and SECRET not in dump
    assert client.cookies.get('__Host-dangkou_app_session') not in dump
    assert client.post('/app-entry/exchange', headers=BROWSER, json={'code': code}).status_code == 401
    assert client.get('/categories', headers=BROWSER).status_code == 200


def test_parallel_exchange_consumes_once(client):
    code = code_of(mint(client))
    def exchange(_):
        with TestClient(client.app, base_url=ORIGIN) as other:
            return other.post('/app-entry/exchange', headers=BROWSER, json={'code': code}).status_code
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(exchange, range(2))) == [200, 401]


def test_backend_auth_and_fixed_origin(client):
    payload = {'actorId': 'u', 'upstreamSessionId': 's', 'requestId': 'r'}
    assert client.post('/app-entry/tickets', json=payload).status_code == 401
    assert client.post('/app-entry/tickets', headers={**BACKEND, 'Origin': 'https://evil.example'}, json=payload).status_code == 403
    r = client.post('/app-entry/tickets', headers={**BACKEND, 'Host': 'evil.example', 'X-Forwarded-Host': 'evil.example'}, json=payload)
    assert r.status_code == 200 and r.json()['loginUrl'].startswith(ORIGIN)
    assert mint(client, actorId='').status_code == 422
    assert mint(client, actorId='x' * 500).status_code == 422
    assert mint(client, returnUrl='https://evil.example').status_code == 422


def test_expired_ticket_and_session(client):
    code = code_of(mint(client))
    conn = client.app.state.conn
    conn.execute('UPDATE app_entry_tickets SET expires_at=0'); conn.commit()
    assert client.post('/app-entry/exchange', headers=BROWSER, json={'code': code}).status_code == 401
    login(client, requestId='second')
    conn.execute('UPDATE app_entry_sessions SET expires_at=0'); conn.commit()
    assert client.get('/categories', headers=BROWSER).status_code == 401


def test_origin_csrf_logout_and_no_stale_credential_fallback(client):
    code = code_of(mint(client))
    assert client.post('/app-entry/exchange', headers={'Origin': 'https://evil.example'}, json={'code': code}).status_code == 403
    assert client.post('/app-entry/exchange', json={'code': code}).status_code == 403
    assert client.post('/app-entry/exchange', headers=BROWSER, json={'code': code}).status_code == 200
    assert client.post('/app-entry/logout', headers=BROWSER).status_code == 403
    h = app_headers(client)
    assert client.post('/app-entry/logout', headers={**h, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/app-entry/logout', headers=h).status_code == 200
    assert client.get('/categories', headers={**BROWSER, 'X-Service-Token': client.app.state.token}).status_code == 401
    assert client.get('/categories', headers={'X-Service-Token': client.app.state.token}).status_code == 200


def test_revoke_targets_upstream_session_and_pending_tickets(client):
    login(client)
    code = code_of(mint(client, requestId='pending'))
    r = client.post('/app-entry/revoke', headers=BACKEND, json={'upstreamSessionId': 'app-session-1'})
    assert r.status_code == 200
    assert client.get('/categories', headers=BROWSER).status_code == 401
    assert client.post('/app-entry/exchange', headers=BROWSER, json={'code': code}).status_code == 401
    login(client, upstreamSessionId='different', requestId='other')
    assert client.get('/categories', headers=BROWSER).status_code == 200


@pytest.mark.parametrize('key,value', [('CATALOG_APP_ACCESS_ENABLED','0'), ('CATALOG_APP_ENTRY_SECRET','rotated-'+SECRET), ('CATALOG_APP_SHOP_ID','shop-two')])
def test_disable_rotation_and_identity_invalidate_session(client, monkeypatch, key, value):
    login(client)
    monkeypatch.setenv(key,value)
    assert client.get('/categories', headers=BROWSER).status_code == 401
    client.cookies.clear()
    assert client.get('/categories', headers={'X-Service-Token': client.app.state.token}).status_code == 200


@pytest.mark.parametrize('base', ['http://shop.example', 'https://shop.example/nested', 'https://user:pass@shop.example', 'https://shop.example/?x=1'])
def test_bad_deployment_config_fails_closed(client, monkeypatch, base):
    monkeypatch.setenv('CATALOG_APP_PUBLIC_BASE_URL',base)
    assert mint(client).status_code == 503


def test_cookie_is_not_service_or_customer_credential(client):
    login(client)
    for url in ['/ready', '/shop/linkage', '/cs/catalog']:
        assert client.get(url, headers=BROWSER).status_code in (401,403)
    assert client.post('/import', headers=app_headers(client), json={}).status_code == 403


def test_existing_product_edit_review_and_media_work(client):
    rows = seed_products(client.app.state.conn,[{'data':{'model':'before'}}])
    product_id = rows[0]['id']
    client.app.state.storage.save('test_cat', product_id, 'photo.png', b'photo')
    login(client)
    h=app_headers(client)
    assert client.get('/products/test_cat', headers=h).status_code == 200
    image_url=f'/img/test_cat/{product_id}/photo.png'
    assert client.get(image_url).content == b'photo'
    assert client.get('/categories/test_cat/template.xlsx').status_code == 200
    assert client.patch(f'/products/test_cat/{product_id}', headers=BROWSER, json={'changes':{'型号':'after'}}).status_code == 403
    edit=client.patch(f'/products/test_cat/{product_id}', headers=h, json={'changes':{'型号':'after'}})
    assert edit.status_code == 200,edit.text
    body={'token':edit.json()['token'],'approved':True}
    decision=f"/tickets/{edit.json()['ticket_id']}/decision"
    assert client.post(decision, headers=BROWSER, json=body).status_code == 403
    assert client.post(decision, headers=h, json=body).status_code == 200
    assert client.get('/products/test_cat',headers=h).json()['products'][0]['型号']=='after'
    buf=io.BytesIO(); Image.new('RGB',(2,2)).save(buf,'PNG')
    assert client.post('/upload',headers=h,files={'file':('photo.png',buf.getvalue(),'image/png')}).status_code==200
    assert client.post('/app-entry/logout',headers=h).status_code==200
    assert client.post(decision,headers=BROWSER,json=body).status_code==401
    assert client.get(image_url).status_code==401


def test_legacy_proxy_serves_auth_helper_without_credentials(client, monkeypatch):
    # HTML includes this relative public asset; script requests don't carry the
    # ?t= credential from the page URL. It must work when APP login is disabled.
    from catalog import merchant_binding
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
    merchant_binding.register(client.app)
    result=client.get('/merchant/manage/'+'a'*24+'/app-auth.js')
    assert result.status_code==200
    assert 'createCatalogAuth' in result.text


def test_real_webpage_app_login_and_legacy_login(client, monkeypatch):
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright, expect
    client.app.mount('/', StaticFiles(directory=str(Path(__file__).parents[1]/'static'),html=True))
    rows=seed_products(client.app.state.conn,[{'data':{'model':'APP商品'}}])
    login_url=mint(client).json()['loginUrl']
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route_api(route):
            request=route.request
            client.cookies.clear() # Let browser cookies, not TestClient state, authenticate.
            response=client.request(request.method,request.url,
                                    headers=request.all_headers(),content=request.post_data_buffer)
            headers={k:v for k,v in response.headers.items() if k not in ('content-encoding','content-length')}
            route.fulfill(status=response.status_code,headers=headers,body=response.content)
            client.cookies.clear()
        page.route(ORIGIN+'/**',route_api)
        page.goto(login_url)
        page.wait_for_url(ORIGIN+'/')
        page.locator('#v-products').click()
        expect(page.locator('#plist')).to_contain_text('APP商品')
        page.locator('#plist').get_by_role('button',name='编辑',exact=True).first.click()
        expect(page.locator('#modal')).to_be_visible()
        assert not errors
        page.evaluate('closeForm()')
        # Fresh old-style entry still works within the same WebView/cookie jar.
        page.goto(ORIGIN+'/?t='+client.app.state.token)
        page.locator('#v-products').click()
        expect(page.locator('#plist')).to_contain_text('APP商品')
        assert not errors
        browser.close()


def test_default_https_port_and_host_case_are_canonical(client, monkeypatch):
    monkeypatch.setenv('CATALOG_APP_PUBLIC_BASE_URL','https://SHOP.EXAMPLE:443/')
    issued=mint(client)
    assert issued.status_code==200
    assert issued.json()['loginUrl'].startswith('https://shop.example/app-entry/')
    code=parse_qs(urlsplit(issued.json()['loginUrl']).fragment)['code'][0]
    assert client.post('/app-entry/exchange',headers={'Origin':'https://shop.example'},json={'code':code}).status_code==200


def test_entry_secret_cannot_reuse_service_credential(client):
    client.app.state.token=SECRET
    assert mint(client).status_code==503


def test_cookie_only_write_needs_csrf_and_legacy_cookie_coexistence(client):
    login(client)
    original=dict(client.cookies)
    assert client.post('/categories',json={'name':'x','fields':[]}).status_code==403
    client.post('/app-entry/revoke',headers=BACKEND,json={'upstreamSessionId':'app-session-1'})
    assert client.get('/categories').status_code==401
    assert client.get('/categories',headers={'X-Service-Token':client.app.state.token}).status_code==200
    assert dict(client.cookies)==original


def test_copied_ticket_and_cookie_do_not_authenticate_independent_shop(client, tmp_path):
    code=code_of(mint(client))
    login(client,requestId='another')
    other=FastAPI()
    other.state.conn=db.connect(str(tmp_path/'other.db'))
    db.init_db(other.state.conn)
    other.state.token='other-service'
    other.state.storage=LocalStorage(str(tmp_path/'other-images'))
    other.state.callback=None
    register_routes(other)
    try:
        with TestClient(other,base_url=ORIGIN) as c:
            c.cookies.update(client.cookies)
            assert c.get('/categories',headers=BROWSER).status_code==401
            assert c.post('/app-entry/exchange',headers=BROWSER,json={'code':code}).status_code==401
    finally:
        other.state.conn.close()
