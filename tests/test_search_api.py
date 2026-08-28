import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from catalog import db, ingest
from catalog.main import app
from catalog.storage import LocalStorage


def _parse_with_image(base):
    def f(cat, p, wd):
        open(wd + '/r3_c1.png', 'wb').write(b'PNG')
        return {'vendor': '厂',
                'products': [{'model_no': '8225', 'image_main': 'r3_c1.png'}]}
    return f


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.agent, 'parse', _parse_with_image(str(tmp_path)))
    import catalog.api as api_mod
    monkeypatch.setattr(api_mod, 'embed_image_stub', None, raising=False)
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    app.state.conn = conn
    app.state.token = ''
    app.state.storage = st
    app.state.callback = None
    monkeypatch.setattr('catalog.search.embed_image', lambda b: [1.0] + [0.0] * 1023)
    return TestClient(app)


def _import_and_approve(client):
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


def test_approve_persists_image_and_embeds(client):
    _import_and_approve(client)
    p = client.get('/products/razor').json()['products'][0]
    assert p['主图'].startswith('razor/')           # 已落位 storage rel
    r = client.get(f"/img/{p['主图']}")
    assert r.status_code == 200 and r.content == b'PNG'


def test_search_endpoint(client, tmp_path):
    _import_and_approve(client)
    q = tmp_path / 'q.png'
    q.write_bytes(b'Q')
    r = client.post('/search', json={'image_path': str(q)})
    assert r.status_code == 200
    hits = r.json()['hits']
    assert hits and hits[0]['fields']['产品型号'] == '8225'
    assert hits[0]['inner_code'].startswith('KS-')
