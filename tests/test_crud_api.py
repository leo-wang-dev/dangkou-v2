import sqlite3

import pytest
from fastapi.testclient import TestClient

from catalog import db, ingest
from catalog.main import app
from catalog.storage import LocalStorage


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.agent, 'parse',
                        lambda cat, p, wd: {'vendor': '厂',
                                            'products': [{'model_no': '8225', 'price': '21.5'}]})
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = ''
    st = LocalStorage(str(tmp_path))
    app.state.storage = st
    app.state.callback = None
    return TestClient(app)


def _import_and_approve(client):
    import time
    f_ok = False
    # 直接调 ingest（绕文件存在检查用 /import 也行，此处走 API 更真）
    r = client.post('/import', json={'path': '/tmp/none.xlsx', 'category': 'razor'})
    assert r.status_code == 404  # 文件不存在拒收
    # 用真临时文件
    import tempfile
    f = tempfile.mktemp(suffix='.xlsx')
    open(f, 'wb').write(b'x')
    doc = client.post('/import', json={'path': f, 'category': 'razor'}).json()['doc_id']
    for _ in range(100):
        time.sleep(0.05)
        if client.get(f'/import/{doc}').json()['status'] != 'parsing':
            break
    tk = client.get('/tickets').json()['tickets'][0]
    client.post(f"/tickets/{tk['id']}/decision",
                json={'token': tk['token'], 'approved': True})
    return doc


def test_import_flow_to_products(client):
    _import_and_approve(client)
    r = client.get('/products/razor').json()
    assert r['template']['name'] == '剃须刀'
    assert len(r['products']) == 1 and r['products'][0]['产品型号'] == '8225'


def test_patch_creates_mutate_ticket_not_direct_write(client):
    _import_and_approve(client)
    pid = client.get('/products/razor').json()['products'][0]['id']
    r = client.patch(f'/products/razor/{pid}', json={'changes': {'price': '23'}})
    assert r.json()['ticket_id']
    assert client.get('/products/razor').json()['products'][0]['报价'] == '21.5'  # 未批不改
    mt = [t for t in client.get('/tickets').json()['tickets']
          if t['ticket_type'] == 'mutate'][0]
    client.post(f"/tickets/{mt['id']}/decision",
                json={'token': mt['token'], 'approved': True})
    assert client.get('/products/razor').json()['products'][0]['报价'] == '23'


def test_delete_creates_delist_ticket(client):
    _import_and_approve(client)
    pid = client.get('/products/razor').json()['products'][0]['id']
    client.delete(f'/products/razor/{pid}')
    dt = [t for t in client.get('/tickets').json()['tickets']
          if t['ticket_type'] == 'mutate'][0]
    client.post(f"/tickets/{dt['id']}/decision",
                json={'token': dt['token'], 'approved': True})
    assert client.get('/products/razor').json()['products'][0]['状态'] == 'delisted'
