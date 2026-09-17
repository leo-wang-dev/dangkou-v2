"""C端字段通道：阶梯价(tier_price)/可观测(cs_visible) 可经 mutate 工单录入。

规则（v1.2 判定①+开关约束）：开可观测 ⇒ 阶梯价必填且可解析；否则 400（B层）让 AI 自纠。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from catalog import db, tickets
from catalog.main import app
from catalog.templates import TEMPLATES


@pytest.fixture()
def client():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = ''
    app.state.storage = None
    app.state.callback = None
    return TestClient(app)


def _mk_product(client, model='M1'):
    r = client.post('/products/curler', json={'changes': {'ITEM.NO 型号': model, '价格': '10'}})
    assert r.status_code == 200
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    return client.get('/products/curler').json()['products'][0]['id']


def test_cs_keys_legal_in_normalize():
    t = TEMPLATES['curler']
    norm, to_remark = tickets.normalize_changes(
        t, {'阶梯价': '20:12;50:11', '可观测': '1', '随便': 'x'})
    assert norm['tier_price'] == '20:12;50:11'        # 原样保留
    assert norm['cs_visible'] == '1'
    assert to_remark == ['随便'] and '随便：x' in norm['备注']


def test_set_tier_price_via_mutate(client):
    pid = _mk_product(client)
    r = client.patch(f'/products/curler/{pid}', json={'changes': {'阶梯价': '20:12;50:11'}})
    assert r.status_code == 200
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    row = client.app.state.conn.execute(
        'SELECT tier_price, cs_visible FROM product_curler WHERE id=?', (pid,)).fetchone()
    assert row['tier_price'] == '20:12;50:11' and row['cs_visible'] == 0


def test_visible_requires_tier_price(client):
    """开可观测没给（也没已有）合法阶梯价 → 400（AI 自纠），提示口径。"""
    pid = _mk_product(client)
    r = client.patch(f'/products/curler/{pid}', json={'changes': {'可观测': '1'}})
    assert r.status_code == 400
    assert '阶梯价' in r.json()['detail']


def test_visible_ok_when_tier_in_same_changes(client):
    pid = _mk_product(client)
    r = client.patch(f'/products/curler/{pid}',
                     json={'changes': {'可观测': '1', '阶梯价': '20:12'}})
    assert r.status_code == 200
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    row = client.app.state.conn.execute(
        'SELECT cs_visible, tier_price FROM product_curler WHERE id=?', (pid,)).fetchone()
    assert row['cs_visible'] == 1 and row['tier_price'] == '20:12'


def test_visible_ok_when_tier_already_in_db(client):
    pid = _mk_product(client)
    # 先录阶梯价
    r = client.patch(f'/products/curler/{pid}', json={'changes': {'阶梯价': '20:12'}})
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    # 再开可观测
    r = client.patch(f'/products/curler/{pid}', json={'changes': {'可观测': '1'}})
    assert r.status_code == 200


def test_garbage_tier_price_with_visible_rejected(client):
    pid = _mk_product(client)
    r = client.patch(f'/products/curler/{pid}',
                     json={'changes': {'可观测': '1', '阶梯价': '随便写的'}})
    assert r.status_code == 400
