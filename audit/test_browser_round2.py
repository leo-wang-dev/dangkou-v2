"""Real browser clicks against an isolated real API, with service auth enabled.

Only external embedding/model/notification calls are blocked; fixtures replace
vendor parsing and Telegram delivery, not the web/API/database implementation.
Screenshots, request errors and browser traces are retained for every case.
"""
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
import openpyxl
from PIL import Image
from playwright.sync_api import sync_playwright, expect
import pytest

from catalog import db, tickets

ROOT = Path(__file__).resolve().parents[1]
ART = Path(os.environ.get('DANGKOU_AUDIT_ARTIFACT_DIR', str(ROOT / 'audit' / 'fix-verification' / 'browser')))
TOKEN = 'round2-isolated-service-token'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp('audit-browser')
    database = folder / 'catalog.db'
    conn = db.connect(str(database))
    db.init_db(conn)
    conn.close()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'CATALOG_V2_DB': str(database),
           'CATALOG_V2_IMG': str(folder / 'images'),
           'CATALOG_V2_SERVICE_TOKEN': TOKEN, 'CATALOG_NOTIFY_TOKEN': '',
           'BAILIAN_API_KEY': '', 'CATALOG_AGENT_API_KEY': ''}
    # Prevent all external traffic. No remote AI quality is claimed by these tests.
    program = """
import requests, uvicorn
def offline(*a, **kw):
    raise RuntimeError('audit: external HTTP disabled')
requests.sessions.Session.request = offline
from catalog.main import app
uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')
""".replace('PORT', str(port))
    ART.mkdir(parents=True, exist_ok=True)
    with (ART / 'server.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-c', program], cwd=ROOT, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',
                              headers={'X-Service-Token': TOKEN}, trust_env=False) as api:
                for _ in range(100):
                    try:
                        if api.get('/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('isolated browser server did not start')
                yield str(api.base_url).rstrip('/'), database, folder, api
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.fixture
def site(server):
    base, database, folder, api = server
    conn = db.connect(str(database))
    for table in ('product_razor', 'product_curler', 'approval_ticket', 'import_doc',
                  'embedding', 'cs_note', 'cs_link', 'cs_customer', 'cs_conversation_log', 'cs_redline'):
        conn.execute(f'DELETE FROM {table}')
    conn.commit()
    db.init_db(conn)
    conn.execute("INSERT INTO product_razor(id,inner_code,model_no,price,color) "
                 "VALUES('r1','AUDIT-R1','RAZOR-1','7.35','黑色')")
    conn.execute("INSERT INTO product_curler(id,inner_code,item_no,price,tier_price,cs_visible) "
                 "VALUES('c1','AUDIT-C1','CURLER-1','10','20:12;50:11',1)")
    # The photo endpoint currently hardcodes this location, so use its real root.
    photo_root = ROOT / 'data' / 'cs_photos'
    photo_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='audit-browser-', dir=photo_root) as photo_dir:
        photo = Path(photo_dir) / 'sample.jpg'
        Image.new('RGB', (160, 100), 'orange').save(photo)
        for customer in ('a', 'b', 'empty'):
            conn.execute('INSERT INTO cs_customer(id,tg_id) VALUES(?,?)', (customer, customer))
            conn.execute("INSERT INTO cs_link(token,customer_id,expires_at) "
                         "VALUES(?,?,datetime('now','+2 days'))", ('link-' + customer, customer))
            if customer != 'empty':
                conn.execute("INSERT INTO cs_note(customer_id,photo,fields_json,status) "
                             "VALUES(?,?,?,'confirmed')",
                             (customer, str(photo), json.dumps({'型号或品名': '杯子-' + customer,
                                                               '价格': '12', '颜色': '白色'})))
        conn.execute("INSERT INTO cs_link(token,customer_id,expires_at) "
                     "VALUES('expired','a',datetime('now','-1 day'))")
        conn.commit()
        yield {'base': base, 'conn': conn, 'api': api, 'photo': photo, 'folder': folder}
    conn.close()


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, request):
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(4500)
    events = []
    page.on('pageerror', lambda error: events.append({'pageerror': str(error)}))
    page.on('response', lambda r: events.append({'status': r.status, 'url': r.url}) if r.status >= 400 else None)
    page.on('dialog', lambda dialog: dialog.accept())
    name = request.node.name.replace('[', '-').replace(']', '')
    try:
        yield page
    finally:
        page.screenshot(path=str(ART / f'{name}.png'), full_page=True)
        (ART / f'{name}.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
        context.tracing.stop(path=str(ART / f'{name}.zip'))
        context.close()


def products(page, site, category='剃须刀', authenticated=True):
    page.goto(site['base'] + ('/?t=' + TOKEN if authenticated else '/'))
    page.locator('#v-products').click()
    page.locator('#catTabs').get_by_role('button', name=category, exact=True).click()
    expect(page.locator('#plist')).not_to_be_empty()


def submit(page):
    page.locator('#modalBox').get_by_role('button', name='提交', exact=True).click()
    expect(page.locator('#modal')).not_to_be_visible()


def new_import(site, image=False):
    drafts = [{'model_no': 'IMPORT-A', 'price': '12', '_rid': 'n0'},
              {'model_no': 'IMPORT-B', 'price': '13', '_rid': 'n1'}]
    if image:
        drafts[0].update(image_main=site['photo'].name, images=[site['photo'].name])
    return tickets.create(site['conn'], 'import', 'razor',
                          {'kind': 'import', 'work_dir': str(site['photo'].parent),
                           'drafts': {'new': drafts, 'update': [], 'delist': []}})


def review(page, site, ticket):
    page.goto(site['base'] + '/?t=' + TOKEN)
    page.locator(f'button[onclick="loadDetail({ticket["id"]})"]').click()
    return page.locator(f'#detail-{ticket["id"]}')


def new_redline(site):
    result = site['api'].post('/cs/redline', json={'text_raw': '数量低于88个转人工'})
    assert result.status_code == 200
    tid = result.json()['ticket_id']
    return dict(site['conn'].execute('SELECT * FROM approval_ticket WHERE id=?', (tid,)).fetchone())


def test_b_tabs_search_create_edit_delist(page, site):
    products(page, site)
    expect(page.locator('#plist')).to_contain_text('RAZOR-1')
    page.locator('#catTabs').get_by_role('button', name='卷发棒').click()
    expect(page.locator('#plist')).to_contain_text('CURLER-1')
    page.locator('#q').fill('不存在的型号')
    expect(page.locator('#plist')).to_contain_text('暂无商品')
    page.locator('#q').fill('')
    page.get_by_role('button', name='＋ 新增商品').click()
    page.locator('#fg-item_no').fill('NEW-CURLER')
    page.locator('#fg-price').fill('19')
    submit(page)
    card = page.locator('.pcard').filter(has_text='NEW-CURLER')
    expect(card).to_be_visible()
    card.get_by_role('button', name='编辑', exact=True).click()
    page.locator('#fg-price').fill('21')
    submit(page)
    expect(card).to_contain_text('21')
    card.get_by_role('button', name='下架', exact=True).click()
    expect(card).to_have_count(0)
    row = site['conn'].execute("SELECT price,status FROM product_curler WHERE item_no='NEW-CURLER'").fetchone()
    assert tuple(row) == ('21', 'delisted')


def test_b_cancel_edit_does_not_save(page, site):
    products(page, site)
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    page.locator('#fg-price').fill('999')
    page.get_by_role('button', name='取消', exact=True).click()
    page.reload()
    page.locator('#v-products').click()
    expect(page.locator('#plist')).to_contain_text('7.35')


def test_b_clear_field_persists(page, site):
    products(page, site)
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    page.locator('#fg-color').fill('')
    submit(page)
    value = site['conn'].execute("SELECT color FROM product_razor WHERE id='r1'").fetchone()[0]
    assert not value, f'cleared color remained {value!r}'


def test_b_upload_gallery_and_remove_last_image(page, site):
    products(page, site)
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    page.locator('#formFile').set_input_files(str(site['photo']))
    expect(page.locator('.imgman .cell')).to_have_count(1)
    submit(page)
    image = page.locator('.pcard img.big')
    expect(image).to_be_visible()
    image.click()
    expect(page.locator('#lightbox')).to_be_visible()
    page.locator('#lbNext').click()
    page.locator('#lbPrev').click()
    assert page.locator('#lbimg').evaluate('(img) => img.complete && img.naturalWidth > 0')
    page.locator('#lightbox').click(position={'x': 100, 'y': 100})
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    page.locator('.imgman .cell button').click()
    expect(page.locator('.imgman .cell')).to_have_count(0)
    submit(page)
    row = site['conn'].execute("SELECT image_main,images FROM product_razor WHERE id='r1'").fetchone()
    assert not row['image_main'] and json.loads(row['images']) == [], 'last image deletion did not persist'


def test_b_import_edit_partial_reject_approve(page, site):
    ticket = new_import(site, image=True)
    detail = review(page, site, ticket)
    row = page.locator(f'#row-{ticket["id"]}-n0')
    expect(row.locator('img')).to_be_visible()
    row.get_by_role('button', name='编辑', exact=True).click()
    page.locator('#fg-price').fill('22')
    submit(page)
    expect(row).to_contain_text('22')
    page.locator(f'#row-{ticket["id"]}-n1').get_by_role('button', name='驳回', exact=True).click()
    page.get_by_role('button', name='整单通过', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    rows = site['conn'].execute("SELECT model_no,price FROM product_razor WHERE model_no LIKE 'IMPORT-%'").fetchall()
    assert [tuple(r) for r in rows] == [('IMPORT-A', '22')]


def test_b_single_then_whole_approval_no_duplicates(page, site):
    ticket = new_import(site)
    review(page, site, ticket)
    page.locator(f'#row-{ticket["id"]}-n0').get_by_role('button', name='✓通过', exact=True).click()
    expect(page.locator(f'#row-{ticket["id"]}-n0')).to_contain_text('已✓')
    page.get_by_role('button', name='整单通过', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    counts = site['conn'].execute("SELECT model_no,COUNT(*) FROM product_razor WHERE model_no LIKE 'IMPORT-%' GROUP BY model_no").fetchall()
    assert [tuple(r) for r in counts] == [('IMPORT-A', 1), ('IMPORT-B', 1)]


def test_b_whole_reject_leaves_products_unchanged(page, site):
    ticket = new_import(site)
    review(page, site, ticket)
    page.get_by_role('button', name='整单驳回', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    assert site['conn'].execute("SELECT COUNT(*) FROM product_razor WHERE model_no LIKE 'IMPORT-%'").fetchone()[0] == 0


def test_b_mutate_card_approve(page, site):
    result = site['api'].patch('/products/razor/r1', json={'changes': {'报价': '18'}})
    assert result.status_code == 200
    ticket = {'id': result.json()['ticket_id']}
    detail = review(page, site, ticket)
    expect(detail).to_contain_text('7.35')
    expect(detail).to_contain_text('18')
    detail.get_by_role('button', name='✓通过', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    assert site['conn'].execute("SELECT price FROM product_razor WHERE id='r1'").fetchone()[0] == '18'


def test_b_redline_ticket_has_readable_detail(page, site):
    ticket = new_redline(site)
    detail = review(page, site, ticket)
    expect(detail).to_contain_text('数量低于88个转人工')


def test_b_unauthenticated_page_hides_internal_products(page, site):
    page.goto(site['base'] + '/')
    expect(page.locator('#review')).to_contain_text('管理链接无效')
    expect(page.locator('body')).not_to_contain_text('7.35')


def test_b_tier_visibility_fields_can_be_maintained(page, site):
    products(page, site, category='卷发棒')
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    expect(page.locator('#modalBox')).not_to_contain_text('阶梯价')
    expect(page.locator('#modalBox')).to_contain_text('可观测')


def test_b_mobile_layout_fits_viewport(page, site):
    page.set_viewport_size({'width': 375, 'height': 812})
    products(page, site)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'product grid overflows mobile viewport'


@pytest.mark.parametrize('viewport', ['desktop', 'mobile'])
def test_c_list_edit_refresh_export(page, site, viewport):
    if viewport == 'mobile':
        page.set_viewport_size({'width': 375, 'height': 812})
    page.goto(site['base'] + '/cs/list.html?k=link-a')
    cell = page.locator('td[data-field="价格"]')
    expect(cell).to_have_text('12')
    cell.click()
    cell.fill('2.5')
    cell.blur()
    expect(page.locator('#tip')).to_contain_text('已保存')
    page.get_by_role('button', name='↻ 刷新').click()
    expect(cell).to_have_text('2.5')
    with page.expect_download() as result:
        page.get_by_role('button', name='⬇️ 导出 Excel').click()
    wb = openpyxl.load_workbook(io.BytesIO(Path(result.value.path()).read_bytes()))
    headers = [c.value for c in wb.active[1]]
    assert wb.active.cell(2, headers.index('价格') + 1).value == '2.5'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('token', ['missing', 'expired'])
def test_c_invalid_or_expired_link_shows_error(page, site, token):
    page.goto(site['base'] + '/cs/list.html?k=' + token)
    expect(page.locator('#list')).to_contain_text('链接无效或已过期')


def test_c_empty_list_has_guidance(page, site):
    page.goto(site['base'] + '/cs/list.html?k=link-empty')
    expect(page.locator('#list')).to_contain_text('先在 Telegram 里拍照')


def test_c_customer_switch_does_not_mix_list(page, site):
    page.goto(site['base'] + '/cs/list.html?k=link-a')
    expect(page.locator('#list')).to_contain_text('杯子-a')
    expect(page.locator('#list')).not_to_contain_text('杯子-b')
    page.goto(site['base'] + '/cs/list.html?k=link-b')
    expect(page.locator('#list')).to_contain_text('杯子-b')
    expect(page.locator('#list')).not_to_contain_text('杯子-a')


@pytest.mark.parametrize('explicit_photo_column', [False, True])
def test_c_photo_visible_with_service_auth_enabled(page, site, explicit_photo_column):
    if explicit_photo_column:
        site['conn'].execute("UPDATE cs_note SET fields_json=? WHERE customer_id='a'",
                             (json.dumps({'型号或品名': '杯子-a', '图片': '见照片', '价格': '12'}),))
        site['conn'].commit()
    page.goto(site['base'] + '/cs/list.html?k=link-a')
    expect(page.locator('#list table')).to_be_visible()
    page.wait_for_function("!!document.querySelector('#list img') && document.querySelector('#list img').naturalWidth > 0")


def test_c_download_contains_photo(page, site):
    page.goto(site['base'] + '/cs/list.html?k=link-a')
    expect(page.locator('table')).to_be_visible()
    with page.expect_download() as result:
        page.get_by_role('button', name='⬇️ 导出 Excel').click()
    wb = openpyxl.load_workbook(io.BytesIO(Path(result.value.path()).read_bytes()))
    assert len(wb.active._images) == 1


@pytest.mark.parametrize('approve', [True, False])
def test_redline_card_decision_click(page, site, approve):
    ticket = new_redline(site)
    page.goto(f'{site["base"]}/cs/redline.html?i={ticket["id"]}&t={ticket["token"]}')
    expect(page.locator('#old')).to_contain_text('账期')
    expect(page.locator('#new')).to_contain_text('88')
    page.get_by_role('button', name='批准生效' if approve else '驳回', exact=True).click()
    expect(page.locator('#done')).to_contain_text('已批准' if approve else '已驳回')
    text = site['api'].get('/cs/redline').json()['text_raw']
    assert ('88' in text) == approve


def test_redline_card_wrong_token_cannot_approve(page, site):
    ticket = new_redline(site)
    page.goto(f'{site["base"]}/cs/redline.html?i={ticket["id"]}&t=wrong')
    page.get_by_role('button', name='批准生效', exact=True).click()
    expect(page.locator('#done')).to_contain_text('操作失败')
    assert '88' not in site['api'].get('/cs/redline').json()['text_raw']


def test_redline_card_repeated_approval_denied(page, site):
    ticket = new_redline(site)
    page.goto(f'{site["base"]}/cs/redline.html?i={ticket["id"]}&t={ticket["token"]}')
    page.get_by_role('button', name='批准生效', exact=True).click()
    expect(page.locator('#done')).to_contain_text('已批准')
    page.reload()
    page.get_by_role('button', name='批准生效', exact=True).click()
    expect(page.locator('#done')).to_contain_text('操作失败')


def test_redline_missing_ticket_has_error(page, site):
    page.goto(site['base'] + '/cs/redline.html?i=999999&t=wrong')
    expect(page.locator('#old')).to_contain_text('工单不存在或已过期')


def test_shop_contact_approval_click(page, site):
    response = site['api'].patch('/shop', json={'changes': {
        'owner_tg_username': 'owner_test', 'owner_wechat': 'wx-owner-test', 'address': '测试路18号'}})
    assert response.status_code == 200
    tid = response.json()['ticket_id']
    detail = review(page, site, {'id':tid})
    expect(page.locator('#review')).to_contain_text('老板联系方式')
    expect(detail).to_contain_text('wx-owner-test')
    detail.locator('..').get_by_role('button', name='整单通过', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    expected = {
        'owner_tg_username':'owner_test', 'owner_wechat':'wx-owner-test', 'address':'测试路18号',
        'business_hours':'', 'shipping_info':'', 'faq':''}
    profile=site['api'].get('/shop').json()
    assert {k:profile[k] for k in expected} == expected
    assert profile['shop_id']


def test_tier_edit_persists_and_reopens(page, site):
    products(page, site, category='卷发棒')
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    expect(page.locator('#fg-tier_price')).to_have_count(0)
    page.locator('#fg-cs_visible').fill('1')
    submit(page)
    page.locator('.pcard').get_by_role('button', name='编辑').click()
    expect(page.locator('#fg-tier_price')).to_have_count(0)
    expect(page.locator('#fg-cs_visible')).to_have_value('1')
