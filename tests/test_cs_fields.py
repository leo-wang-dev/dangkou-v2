"""可观测无需阶梯价；旧阶梯字段和工单必须拒绝。"""
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
    app.state.token = 'test-service-token'
    app.state.storage = None
    app.state.callback = None
    return TestClient(app, headers={'X-Service-Token': 'test-service-token'})


def _mk_product(client, model='M1'):
    r = client.post('/products/curler', json={'changes': {'ITEM.NO 型号': model, '价格': '10'}})
    assert r.status_code == 200
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    return client.get('/products/curler').json()['products'][0]['id']


def test_visibility_no_longer_requires_prices(client):
    pid=_mk_product(client)
    r=client.patch(f'/products/curler/{pid}',json={'changes':{'可观测':'1'}})
    assert r.status_code==200
    tk=r.json()
    assert client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True}).status_code==200
    assert client.app.state.conn.execute('SELECT cs_visible FROM product_curler WHERE id=?',(pid,)).fetchone()[0]==1


@pytest.mark.parametrize('key',['阶梯价','tier_price','阶梯报价'])
def test_tier_configuration_removed_from_all_write_routes(client,key):
    pid=_mk_product(client)
    for path,method in [(f'/products/curler/{pid}','patch'),('/products/curler','post'),(f'/products/curler/{pid}/direct','patch'),('/products/curler/direct','post')]:
        r=getattr(client,method)(path,json={'changes':{'item_no':'X',key:'20:12'}})
        assert r.status_code==400
    result=client.get('/products/curler').json()
    assert all(f['col']!='tier_price' for f in result['template']['fields'])
    assert all('阶梯价' not in row for row in result['products'])


def test_old_tier_ticket_cannot_be_approved(client):
    pid=_mk_product(client);conn=client.app.state.conn
    tk=tickets.create(conn,'mutate','curler',{'kind':'mutate','action':'update','product_id':pid,'changes':{'tier_price':'20:12'}})
    r=client.post(f"/tickets/{tk['id']}/decision",json={'token':tk['token'],'approved':True})
    assert r.status_code==400
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(tk['id'],)).fetchone()[0]=='pending'
