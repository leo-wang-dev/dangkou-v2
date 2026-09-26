import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from catalog import agent, db
from catalog.main import app
from catalog.storage import LocalStorage
from tests.test_workbook_templates import blowdryer_fixture


def _model_key(template):
    return next(f['key'] for f in template['fields'] if f.get('role') == 'model')


def _parse_with_image(template, xlsx_path, work_dir, *, sheet=''):
    open(f'{work_dir}/r3_c1.png', 'wb').write(b'PNG')
    return {'vendor': '厂',
            'products': [{_model_key(template): '8225', 'image_main': 'r3_c1.png'}]}


def _parse_with_imgs(template, xlsx_path, work_dir, *, sheet=''):
    """一张 png + 一张 tif（KS-5390 案：浏览器不渲染 tif，落位时须转 png）。"""
    from PIL import Image
    open(f'{work_dir}/a.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (200, 30, 30)).save(buf, format='TIFF')
    open(f'{work_dir}/b.tif', 'wb').write(buf.getvalue())
    return {'vendor': '厂', 'products': [
        {_model_key(template): 'M1', 'image_main': 'a.png', 'images': ['a.png', 'b.tif']}]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    app.state.conn = conn
    app.state.token = 'test-service-token'
    app.state.storage = st
    app.state.callback = None
    monkeypatch.setattr('catalog.search.embed_image', lambda b, *a, **k: [1.0] + [0.0] * 1023)
    return TestClient(app, headers={'X-Service-Token': 'test-service-token'})


def _import_and_approve(client, tmp_path, parse=_parse_with_image):
    from catalog import agent
    agent.parse_dynamic = parse          # 覆盖 conftest 的离线替身（本测试自带图）
    path = blowdryer_fixture(tmp_path)
    doc = client.post('/import', json={'path': str(path), 'phase': 'template',
                                       'mode': 'new', 'wait': True,
                                       'source_key': 'vendor-a'}).json()
    assert doc['status'] == 'ticketed', doc
    t = next(t for t in client.get('/tickets').json()['tickets'] if t['status'] == 'pending')
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    result = client.post('/import', json={'path': str(path), 'phase': 'products',
                                          'mode': 'new', 'template_doc_id': doc['doc_id'],
                                          'wait': True, 'source_key': 'vendor-a'}).json()
    assert result['status'] == 'ticketed', result
    pt = next(t for t in client.get('/tickets').json()['tickets'] if t['status'] == 'pending')
    client.post(f"/tickets/{pt['id']}/decision", json={'token': pt['token'], 'approved': True})
    return path


def test_approve_persists_image_and_embeds(client, tmp_path):
    _import_and_approve(client, tmp_path)
    category = client.get('/categories').json()['categories'][0]['key']
    p = client.get(f'/products/{category}').json()['products'][0]
    assert p['主图'].startswith(category + '/')         # 已落位 storage rel
    r = client.get(f"/img/{p['主图']}")
    assert r.status_code == 200 and r.content == b'PNG'
    assert client.app.state.conn.execute(
        'SELECT COUNT(*) c FROM embedding').fetchone()['c'] == 1


def test_search_endpoint(client, tmp_path):
    _import_and_approve(client, tmp_path)
    q = tmp_path / 'q.png'
    from PIL import Image
    Image.new('RGB', (8, 8), 'blue').save(q)
    r = client.post('/search', json={'image_path': str(q)})
    assert r.status_code == 200
    hits = r.json()['hits']
    assert hits and hits[0]['fields']['产品型号'] == '8225'
    assert hits[0]['inner_code'].startswith('KS-')


def test_persist_multi_images_and_tif_conversion(client, tmp_path):
    """多图全落盘 + TIFF转PNG（KS-5390 案：浏览器不渲染tif）。"""
    _import_and_approve(client, tmp_path, parse=_parse_with_imgs)
    category = client.get('/categories').json()['categories'][0]['key']
    p = client.get(f'/products/{category}').json()['products'][0]
    rels = p.get('图集') or []
    assert len(rels) == 2, rels                       # 两张都落位
    assert all(r.endswith(('.png', '.jpg', '.jpeg')) for r in rels)  # tif已转png
    for r in rels:
        assert client.get(f'/img/{r}').status_code == 200
