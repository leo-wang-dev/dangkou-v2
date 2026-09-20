"""Merchant H5 guide: navigate without sending Bot credentials to the browser."""
import json

from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, TOKEN


def test_customer_bot_guide_navigation_and_safe_status(page, site):
    seen = []

    def status(route):
        seen.append(route.request.headers.get('x-service-token'))
        route.fulfill(
            content_type='application/json', body=json.dumps({
                'enabled': True, 'bot_username': 'TestShop_bot',
                'bot_url': 'https://t.me/TestShop_bot', 'runtime_status': 'running'}))

    page.route('**/wechat/customer-bot/status', status)
    page.goto(site['base'] + '/?t=' + TOKEN)
    assert page.evaluate('location.search') == ''
    page.locator('#v-products').click()
    page.get_by_text('开通客户 Telegram Bot', exact=True).click()
    guide = page.locator('#tg-guide')
    expect(guide).to_contain_text('/newbot')
    expect(guide).to_contain_text('绑定客户Bot')
    expect(guide).to_contain_text('微信')
    expect(guide.locator('input,textarea')).to_have_count(0)
    expect(page.locator('#tg-bot-status')).to_contain_text('运行中')
    expect(page.locator('#tg-bot-link')).to_have_attribute('href', 'https://t.me/TestShop_bot')
    assert seen == [TOKEN]
    page.locator('#catTabs').get_by_text('卷发棒', exact=True).click()
    expect(page.locator('#plist')).to_contain_text('CURLER-1')
    page.locator('#v-review').click()
    expect(page.locator('#products')).to_be_hidden()
    page.locator('#v-products').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(guide).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_unavailable_status_keeps_guide_and_product_management(page, site):
    page.route('**/wechat/customer-bot/status', lambda route: route.fulfill(status=404))
    page.goto(site['base'] + '/?t=' + TOKEN)
    page.locator('#v-products').click()
    expect(page.locator('#tg-bot-status')).to_contain_text('暂无法读取')
    page.get_by_text('开通客户 Telegram Bot', exact=True).click()
    expect(page.locator('#tg-guide')).to_contain_text('BotFather')
    expect(page.locator('#plist')).to_contain_text('RAZOR-1')


import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.fixture
def prefixed_wechat_server(tmp_path):
    """Real API behind the same strip-prefix arrangement as the isolated nginx site."""
    from catalog import db
    database = tmp_path / 'wechat.db'
    conn = db.connect(str(database)); db.init_db(conn)
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('guide-customer','guide-100')")
    conn.execute("INSERT INTO cs_note(customer_id,photo,fields_json,status) VALUES('guide-customer','','{\"型号或品名\":\"前缀测试\",\"数量\":\"12\"}','confirmed')")
    conn.execute("INSERT INTO cs_link(token,customer_id) VALUES('guide-link','guide-customer')")
    conn.commit(); conn.close()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    prefix = '/merchant/manage/' + 'e' * 24
    customer_prefix = '/merchant/customer/' + 'e' * 24
    program = f'''from fastapi import FastAPI
from catalog.main import app
import uvicorn
outer = FastAPI()
outer.mount({prefix!r}, app)
outer.mount({customer_prefix!r}, app)
outer.mount('/', app)
uvicorn.run(outer, host='127.0.0.1', port={port}, log_level='warning')
'''
    env = {**os.environ, 'CATALOG_V2_DB': str(database), 'CATALOG_V2_SERVICE_TOKEN': TOKEN,
           'WECHAT_CUSTOMER_BOT_ENABLED': '1', 'WECHAT_CUSTOMER_STATE': str(tmp_path/'state'),
           'CATALOG_NOTIFY_TOKEN': '', 'BAILIAN_API_KEY': '', 'CATALOG_AGENT_API_KEY': ''}
    root = Path(__file__).resolve().parents[2]
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-c', program], cwd=root, env=env, stdout=log, stderr=log)
        base = f'http://127.0.0.1:{port}'
        try:
            with httpx.Client(trust_env=False) as health:
                for _ in range(100):
                    try:
                        if health.get(base+prefix+'/health').status_code == 200: break
                    except httpx.TransportError: pass
                    time.sleep(.05)
                else: raise RuntimeError('prefixed API failed to start')
            yield base, prefix, customer_prefix
        finally:
            process.terminate(); process.wait(timeout=10)


def test_real_unbound_status_and_management_prefix_redline(page, prefixed_wechat_server):
    base, prefix, _ = prefixed_wechat_server
    requested = []
    page.on('request', lambda request: requested.append(request.url))
    with httpx.Client(base_url=base+prefix, headers={'X-Service-Token':TOKEN}, trust_env=False) as api:
        response = api.get('/wechat/customer-bot/status')
        assert response.status_code == 200
        assert response.json()['runtime_status'] == 'unbound'
        page.goto(base+prefix+'/?t='+TOKEN)
        assert page.evaluate('location.search') == ''
        page.locator('#v-products').click()
        expect(page.locator('#tg-bot-status')).to_contain_text('尚未绑定')
        page.get_by_text('开通客户 Telegram Bot', exact=True).click()
        expect(page.locator('#tg-guide')).to_contain_text('绑定客户Bot')
        page.set_viewport_size({'width': 390, 'height': 844})
        pictures = page.locator('#tg-guide figure img')
        expect(pictures).to_have_count(2)
        for picture in pictures.all():
            picture.scroll_into_view_if_needed()
            expect(picture).to_be_visible()
            page.wait_for_function('(img) => img.complete && img.naturalWidth > 0', arg=picture.element_handle())
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        with page.expect_popup() as popup:
            page.get_by_role('link', name='放大查看创建机器人对话截图').click()
        popup.value.wait_for_load_state()
        assert prefix + '/guide/botfather-create.png' in popup.value.url
        assert popup.value.locator('img').evaluate('(img) => img.complete && img.naturalWidth > 0')
        popup.value.close()
        tid = api.post('/cs/redline', json={'text_raw':'加急交货转人工'}).json()['ticket_id']
        ticket = next(t for t in api.get('/tickets').json()['tickets'] if t['id']==tid)
        page.goto(base+prefix+f"/cs/redline.html?i={tid}&t={ticket['token']}")
        expect(page.locator('#new')).to_contain_text('加急交货')
        expect(page.locator('body')).not_to_contain_text('平台公共红线（不可修改）')
        page.get_by_role('button', name='批准生效').click()
        expect(page.locator('#done')).to_contain_text('已批准生效')
        assert api.get('/cs/redline').json()['text_raw'] == '加急交货转人工'
        assert all(base+prefix+'/tickets/' in url for url in requested if '/tickets/' in url)


@pytest.mark.parametrize('path_kind', ['manage', 'customer', 'root'])
def test_customer_list_prefix_edit_and_download(page, prefixed_wechat_server, path_kind):
    base, prefix, customer_prefix = prefixed_wechat_server
    prefix = {'manage':prefix,'customer':customer_prefix,'root':''}[path_kind]
    requested = []
    page.on('request', lambda request: requested.append(request.url))
    page.goto(base+prefix+'/cs/list.html?k=guide-link')
    expect(page.locator('td[data-field="型号或品名"]')).to_have_text('前缀测试')
    cell = page.locator('td[data-field="数量"]')
    cell.fill('25'); cell.blur()
    expect(page.locator('#tip')).to_have_text('已保存 ✓')
    page.reload()
    expect(page.locator('td[data-field="数量"]')).to_have_text('25')
    with page.expect_download() as download:
        page.get_by_role('button', name='⬇️ 导出 Excel').click()
    import openpyxl
    import io
    sheet = openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
    assert '25' in str(list(sheet.values))
    assert all(base+prefix+'/cs/link/' in url for url in requested if '/cs/link/' in url)
