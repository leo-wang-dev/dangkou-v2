import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from catalog import db
from catalog import ingest
from catalog.main import app
from catalog.storage import LocalStorage
from tests.test_workbook_templates import blowdryer_fixture


@pytest.fixture()
def client(tmp_path):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = 'T0KEN'
    app.state.storage = LocalStorage(str(tmp_path))
    app.state.callback = None
    return TestClient(app)


def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200 and r.json()['status'] == 'ready'


def test_template_whole_approval_queues_second_upload_reminder(client, tmp_path):
    path = blowdryer_fixture(tmp_path)
    doc_id = ingest.start(client.app.state.conn, client.app.state.storage, str(path), None,
                          source_key='notify-test', mode='new', phase='template')
    for _ in range(200):
        row = ingest.status(client.app.state.conn, doc_id)
        if row['status'] != 'parsing':
            break
        time.sleep(.01)
    ticket = client.app.state.conn.execute(
        "SELECT * FROM approval_ticket WHERE json_extract(payload,'$.doc_id')=?",
        (doc_id,)).fetchone()
    assert ticket is not None
    response = client.post(f"/tickets/{ticket['id']}/decision",
                           json={'token': ticket['token'], 'approved': True})
    assert response.status_code == 200
    outbox = client.app.state.conn.execute(
        "SELECT body FROM cs_outbox WHERE channel='notify_import' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert outbox is not None and '"approved": true' in outbox['body']


def test_img_requires_token(client, tmp_path):
    client.app.state.storage.save('razor', 'id1', 'main.png', b'PNG')
    assert client.get('/img/razor/id1/main.png').status_code == 401
    r = client.get('/img/razor/id1/main.png', headers={'X-Service-Token': 'T0KEN'})
    assert r.status_code == 200 and r.content == b'PNG'


_TINY_PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ'
             'DwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _query_image(tmp_path):
    img = tmp_path / 'q.png'
    img.write_bytes(__import__('base64').b64decode(_TINY_PNG))
    return str(img)


def test_search_embedding_auth_failure_maps_to_503(client, tmp_path, monkeypatch):
    import requests
    from catalog import search
    resp = requests.models.Response()
    resp.status_code = 401

    def boom(*args, **kwargs):
        raise requests.exceptions.HTTPError(response=resp)
    monkeypatch.setattr(search, 'embed_image', boom)
    r = client.post('/search', headers={'X-Service-Token': 'T0KEN'},
                    json={'image_path': _query_image(tmp_path)})
    assert r.status_code == 503 and '凭据失效' in r.json()['detail']


def test_search_embedding_outage_maps_to_503(client, tmp_path, monkeypatch):
    import requests
    from catalog import search

    def boom(*args, **kwargs):
        raise requests.exceptions.Timeout('embed timeout')
    monkeypatch.setattr(search, 'embed_image', boom)
    r = client.post('/search', headers={'X-Service-Token': 'T0KEN'},
                    json={'image_path': _query_image(tmp_path)})
    assert r.status_code == 503 and '暂时不可用' in r.json()['detail']


def test_empty_preset_categories_are_not_listed(client):
    """空店不显示预置剃须刀/卷发棒：分类清单只含有商品的部分。"""
    r = client.get('/categories', headers={'X-Service-Token': 'T0KEN'})
    assert r.status_code == 200 and r.json()['categories'] == []
    s = client.get('/stats', headers={'X-Service-Token': 'T0KEN'}).json()
    assert s['total'] == 0 and s['by_category'] == {} and s['categories'] == []
    # 显式点名查询仍如实返回 0 款，不 404
    explicit = client.get('/stats', headers={'X-Service-Token': 'T0KEN'},
                          params={'category': 'razor'}).json()
    assert explicit['by_category'] == {'剃须刀': 0}

    client.app.state.conn.execute(
        "INSERT INTO product_razor(id, inner_code, model_no, price) VALUES('r1','KS-1','M1','10')")
    client.app.state.conn.commit()
    r = client.get('/categories', headers={'X-Service-Token': 'T0KEN'})
    assert [c['key'] for c in r.json()['categories']] == ['razor']
    s = client.get('/stats', headers={'X-Service-Token': 'T0KEN'}).json()
    assert s['by_category'] == {'剃须刀': 1}
