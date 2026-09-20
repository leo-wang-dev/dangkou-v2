import base64
import json

import pytest
import requests

from catalog import customer_catalog, photo_inquiry
from tests.test_release_gates import env, auth


def test_customer_read_key_cannot_access_merchant_prices_or_writes(env, monkeypatch):
    conn, client, _ = env
    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN', 'customer-reader')
    conn.execute("INSERT INTO product_curler(id,inner_code,item_no,price,cs_visible) VALUES('hidden','HIDDEN','HIDDEN-MODEL','888.99',0)")
    conn.commit()
    h = {'X-Service-Token': 'customer-reader'}
    assert client.get('/cs/catalog').status_code == 401
    r = client.get('/cs/catalog', headers=h)
    assert r.status_code == 200
    assert len(r.json()['products']) == 1
    assert all(x not in r.text for x in ('7.35', 'tier_price', '20:12', 'HIDDEN-MODEL'))
    assert client.get('/products/curler', headers=h).status_code == 401
    assert client.patch('/shop', headers=h, json={'changes': {'owner_wechat': 'BAD'}}).status_code == 401


def test_bot_calls_merchant_api_and_observes_live_changes(env, monkeypatch):
    conn, client, bot = env
    monkeypatch.setenv('CATALOG_CS_API_URL', 'http://merchant')
    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN', 'customer-reader')
    calls = []

    def transport(_session, method, url, **kwargs):
        path = url.removeprefix('http://merchant')
        calls.append(path)
        return client.request(method, path, headers=kwargs['headers'], json=kwargs['json'])

    monkeypatch.setattr('requests.sessions.Session.request', transport)
    assert 'MODEL-1' in bot._on_text({'id': 'a'}, '有哪些商品')
    conn.execute("UPDATE product_curler SET voltage='230V' WHERE id='p1'")
    conn.commit()
    assert '230V' in bot._on_text({'id': 'a'}, 'MODEL-1 电压是什么')
    conn.execute("UPDATE product_curler SET voltage='110V' WHERE id='p1'")
    conn.commit()
    assert '110V' in bot._on_text({'id': 'a'}, 'MODEL-1 电压是什么')
    assert 'MODEL-1' == photo_inquiry.candidates(conn, [{'型号或品名': 'MODEL-1'}], b'photo')[0]['name']
    assert '/cs/catalog/search' in calls and calls.count('/cs/catalog') >= 3
    conn.execute("UPDATE product_curler SET status='delisted' WHERE id='p1'")
    conn.commit()
    assert 'MODEL-1' not in bot._on_text({'id': 'a'}, '有哪些商品')
    assert photo_inquiry.candidates(conn, [{'型号或品名': 'MODEL-1'}], b'photo') == []


def test_wrong_shop_rejected_and_price_handoff_survives_api_failure(env, monkeypatch):
    conn, _, bot = env
    monkeypatch.setenv('CATALOG_CS_API_URL', 'http://merchant')
    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN', 'customer-reader')
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'shop_id': 'wrong-shop', 'products': []}
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **kw: Response())
    with pytest.raises(customer_catalog.CatalogUnavailable):
        customer_catalog.products(conn)
    def unavailable(*a, **kw):
        raise requests.ConnectionError('offline')
    monkeypatch.setattr('requests.sessions.Session.request', unavailable)
    conn.execute("UPDATE shop_profile SET owner_wechat='KNOWN-OWNER'")
    conn.commit()
    assert 'KNOWN-OWNER' in bot._on_text({'id': 'a'}, 'MODEL-1 多少钱')
    assert 'KNOWN-OWNER' in bot._on_text({'id': 'a'}, '有哪些商品')


def test_purchase_note_enrichment_reads_the_live_merchant_catalog_api(env, monkeypatch):
    from catalog import purchase_notes

    conn, client, bot = env
    monkeypatch.setenv('CATALOG_CS_API_URL', 'http://merchant')
    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN', 'customer-reader')
    calls = []

    def transport(_session, method, url, **kwargs):
        path = url.removeprefix('http://merchant')
        calls.append(path)
        return client.request(method, path, headers=kwargs['headers'], json=kwargs.get('json'))

    monkeypatch.setattr('requests.sessions.Session.request', transport)
    bot.llm.chat_text.side_effect = lambda system, *_args, **_kwargs: (
        json.dumps({'actions': [{'op': 'create', 'fields': {
            '型号或品名': 'MODEL-1', '数量': '100个'}}]}, ensure_ascii=False)
        if '采购记录抽取' in system else '<<PASS>>')
    customer = bot._ensure_customer({'id': 100, 'username': 'buyer'})

    receipt = purchase_notes.capture(bot, customer, '我要MODEL-1 100个')

    note = json.loads(conn.execute(
        "SELECT fields_json FROM cs_note WHERE customer_id=? AND status='draft'",
        (customer['id'],)).fetchone()[0])
    assert '/cs/catalog' in calls
    assert note['商品编号'] == 'p1'
    assert note['型号或品名'] == 'MODEL-1'
    assert note['数量'] == '100个'
    assert '已记录采购笔记' in receipt


def test_customer_list_never_exposes_catalog_binding_ids(env, monkeypatch):
    from catalog import purchase_notes

    conn, client, bot = env
    bot.llm.chat_text.side_effect = lambda system, *_args, **_kwargs: (
        json.dumps({'actions': [{'op': 'create', 'fields': {
            '型号或品名': 'MODEL-1', '数量': '100个'}}]}, ensure_ascii=False)
        if '采购记录抽取' in system else '<<PASS>>')
    customer = bot._ensure_customer({'id': 100, 'username': 'buyer'})
    purchase_notes.capture(bot, customer, '我要MODEL-1 100个')
    conn.execute("INSERT INTO cs_link(token,customer_id,expires_at) VALUES('catalog-note',?,datetime('now','+1 day'))",
                 (customer['id'],))
    conn.commit()

    response = client.get('/cs/link/catalog-note')

    assert response.status_code == 200
    payload = response.json()
    assert 'customer_id' not in payload
    assert 'source_shop_id' not in payload['notes'][0]
    fields = payload['notes'][0]['fields']
    assert fields['型号或品名'] == 'MODEL-1'
    assert '商品编号' not in fields and '商品类别' not in fields
