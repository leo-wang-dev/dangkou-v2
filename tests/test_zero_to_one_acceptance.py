"""A zero-data merchant acceptance path: bind profile, import a new class, set a redline."""
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from catalog import cs, db, dynamic_catalog, dynamic_import, merchant_policy, tickets
from catalog.api import register_routes
from catalog.customer_catalog import local_catalog


def _app():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'zero-test-token'
    app.state.storage = None
    app.state.callback = None
    register_routes(app)
    return app, conn


def _auth():
    return {'X-Service-Token': 'zero-test-token'}


def _approve(client, ticket):
    result = client.post(
        f"/tickets/{ticket['ticket_id']}/decision",
        json={'token': ticket['token'], 'approved': True},
    )
    assert result.status_code == 200, result.text


def test_zero_to_one_shop_dynamic_catalog_and_redline():
    app, conn = _app()
    try:
        with TestClient(app) as client:
            # A newly switched WeChat-managed shop starts with no merchant rule.
            merchant_policy.apply(conn, {
                'wechat_managed': True,
                'shop_name': '',
                'owner_tg_username': '',
                'owner_wechat': '',
            }, 1)
            assert cs.get_redline(conn)['text_raw'] == ''
            assert client.get('/cs/catalog', headers=_auth()).json()['products'] == []

            # Store profile is an approval, not a direct write.
            profile = client.patch('/shop', headers=_auth(), json={'changes': {
                'shop_name': '从零验收档口',
                'owner_tg_username': 'zero_owner',
                'owner_wechat': 'zero-wechat',
            }})
            assert profile.status_code == 200
            _approve(client, profile.json())
            assert client.get('/shop', headers=_auth()).json()['shop_name'] == '从零验收档口'

            # One arbitrary Sheet becomes one dynamic category and two products.
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = '全链路测试分类'
            sheet.append(['产品型号', '颜色', '功率', '成本'])
            sheet.append(['ZERO-A', '黑色', '1600W', '35'])
            sheet.append(['ZERO-B', '白色', '1800W', '37'])
            path = __import__('pathlib').Path('/tmp/zero-to-one-catalog.xlsx')
            workbook.save(path)
            payload = dynamic_import.build_ticket_payload(
                conn, path, path.parent / 'zero-to-one-work', source_key='zero-to-one')
            imported = tickets.create(conn, 'template_import', None, payload)
            tickets.decide(conn, imported['id'], imported['token'], True)
            category = payload['sheets'][0]['template']['key']
            stats = client.get('/stats', headers=_auth()).json()
            assert stats['by_category']['全链路测试分类'] == 2
            assert stats['category_keys']['全链路测试分类'] == category
            customer = local_catalog(conn)['products']
            assert {p['name'] for p in customer} == {'ZERO-A', 'ZERO-B'}
            assert all(set(p.get('specs', {})) == {'产品型号', '颜色', '功率'} for p in customer)

            # Merchant-side visibility can hide one row without deleting it.
            hidden = dynamic_catalog.list_products(conn, category)[1]['id']
            conn.execute('UPDATE product_dynamic SET cs_visible=0 WHERE id=?', (hidden,))
            conn.commit()
            assert len([p for p in local_catalog(conn)['products'] if p['_category'] == category]) == 1
            assert len(dynamic_catalog.list_products(conn, category)) == 2

            # A merchant-approved redline becomes active; without it the same
            # conversation must not be transferred merely because it is risky.
            from types import SimpleNamespace
            calls = []
            bot = SimpleNamespace(
                conn=conn,
                llm=SimpleNamespace(chat_text=lambda prompt, *a, **k: calls.append(prompt) or 'TRANSFER'),
                _resolve_product=lambda *a: (None, None),
                _handoff=lambda *a: '联系老板',
            )
            assert '联系老板' not in merchant_policy.answer(bot, {'id': 'buyer'}, '可以安排货代吗？', False)
            redline = client.post('/cs/redline', headers=_auth(), json={
                'text_raw': '客户问能否安排货代时转人工',
            })
            assert redline.status_code == 200
            _approve(client, redline.json())
            assert '联系老板' in merchant_policy.answer(bot, {'id': 'buyer'}, '可以安排货代吗？', False)
            assert calls and '客户问能否安排货代时转人工' in calls[-1]
            assert 'zero_owner' in cs.contact_reply(conn)
            assert 'zero-wechat' in cs.contact_reply(conn)
    finally:
        conn.close()


def test_zero_to_one_qr_entry_is_h5():
    from pathlib import Path
    page = Path(__file__).parents[1] / 'static' / 'merchant' / 'wechat-bind.html'
    text = page.read_text()
    assert '<meta name="viewport"' in text
    assert 'wechat-binding/start' in text
    assert 'qrPngBase64' in text


def test_find_owner_and_approved_redline_queue_wechat_alerts():
    from types import SimpleNamespace
    from catalog.csbot import CsBot

    app, conn = _app()
    try:
        merchant_policy.apply(conn, {'wechat_managed': True}, 1)
        conn.execute("UPDATE shop_profile SET shop_name='提醒测试档口',owner_wechat='owner-wx',owner_tg_username='owner_tg'")
        conn.commit()
        api = SimpleNamespace()
        bot = CsBot(conn, api, llm=SimpleNamespace(chat_text=lambda *a, **k: 'TRANSFER'))
        customer = bot._ensure_customer({'id': 'buyer-alert'})
        owner_reply = bot._on_text(customer, '找老板')
        assert 'owner-wx' in owner_reply and '@owner_tg' in owner_reply
        first = conn.execute("SELECT * FROM cs_outbox WHERE channel='notify'").fetchall()
        assert len(first) == 1 and '找老板' in first[0]['body']

        cs.set_redline(conn, None, '客户问加急时转人工')
        redline_reply = bot._on_text(customer, '能加急吗')
        assert 'owner-wx' in redline_reply
        second = conn.execute("SELECT * FROM cs_outbox WHERE channel='notify' ORDER BY id").fetchall()
        assert len(second) == 2 and '能加急吗' in second[-1]['body']
    finally:
        conn.close()
