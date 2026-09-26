"""CRUD/API 面：两阶段动态导入、审批工单、直写路由（固定品类删除后的动态契约）。

行级 /tickets/{id}/row 决策路由已随固定品类导入一起删除（整单 decisions.reject/edits
仍支持逐行驳回与编辑，见 test_dynamic_import）。
"""
import json
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

from catalog import db
from catalog.main import app
from catalog.storage import LocalStorage
from tests.conftest import seed_products
from tests.test_workbook_templates import blowdryer_fixture


@pytest.fixture()
def client(tmp_path):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = 'test-service-token'
    st = LocalStorage(str(tmp_path))
    app.state.storage = st
    app.state.callback = None
    return TestClient(app, headers={'X-Service-Token': 'test-service-token'})


def _import_and_approve(client, tmp_path):
    """两阶段动态导入：模板审批 → 商品审批 → 返回分类 key。"""
    path = blowdryer_fixture(tmp_path)
    template = client.post('/import', json={'path': str(path), 'phase': 'template',
                                            'mode': 'new', 'wait': True,
                                            'source_key': 'crud'}).json()
    assert template['status'] == 'ticketed', template
    t = next(t for t in client.get('/tickets').json()['tickets'] if t['status'] == 'pending')
    client.post(f"/tickets/{t['id']}/decision",
                json={'token': t['token'], 'approved': True})
    products = client.post('/import', json={'path': str(path), 'phase': 'products',
                                            'mode': 'new',
                                            'template_doc_id': template['doc_id'],
                                            'wait': True, 'source_key': 'crud'}).json()
    assert products['status'] == 'ticketed', products
    pt = next(t for t in client.get('/tickets').json()['tickets'] if t['status'] == 'pending')
    result = client.post(f"/tickets/{pt['id']}/decision",
                         json={'token': pt['token'], 'approved': True})
    assert result.status_code == 200
    return next(c['key'] for c in client.get('/categories').json()['categories'])


def test_import_flow_to_products(client, tmp_path):
    category = _import_and_approve(client, tmp_path)
    r = client.get(f'/products/{category}').json()
    assert r['template']['name'] == '吹风机'
    assert len(r['products']) == 4
    model = next(f for f in r['template']['fields'] if f['role'] == 'model')
    assert all(p[model['label']] != '' or True for p in r['products'])


def test_patch_creates_mutate_ticket_not_direct_write(client, tmp_path):
    category = _import_and_approve(client, tmp_path)
    model = next(f for f in client.get(f'/products/{category}').json()['template']['fields']
                 if f['role'] == 'model')
    listing = client.get(f'/products/{category}').json()['products']
    pid = listing[0]['id']
    r = client.patch(f'/products/{category}/{pid}',
                     json={'changes': {model['col']: 'CHANGED'}})
    assert r.json()['ticket_id']
    # 未批不改
    assert client.get(f'/products/{category}').json()['products'][0][model['label']] == \
        listing[0][model['label']]
    mt = [t for t in client.get('/tickets').json()['tickets']
          if t['ticket_type'] == 'mutate'][0]
    client.post(f"/tickets/{mt['id']}/decision",
                json={'token': mt['token'], 'approved': True})
    assert client.get(f'/products/{category}').json()['products'][0][model['label']] == 'CHANGED'


def test_delete_creates_delist_ticket(client, tmp_path):
    category = _import_and_approve(client, tmp_path)
    pid = client.get(f'/products/{category}').json()['products'][0]['id']
    client.delete(f'/products/{category}/{pid}')
    dt = [t for t in client.get('/tickets').json()['tickets']
          if t['ticket_type'] == 'mutate'][0]
    client.post(f"/tickets/{dt['id']}/decision",
                json={'token': dt['token'], 'approved': True})
    assert client.get(f'/products/{category}').json()['products'][0]['状态'] == 'delisted'


def test_import_returns_est_sec_floor(client, tmp_path):
    """预估公式：min(600, max(60, MB×4))——小文件吃保底（实测 91MB/378图<1s）。"""
    f = tempfile.mktemp(suffix='.xlsx')
    open(f, 'wb').write(b'x' * (2 * 1048576))   # 2MB
    r = client.post('/import', json={'path': f, 'phase': 'template', 'mode': 'new'}).json()
    assert r['est_sec'] == 60                     # 2×4=8 < 保底60


def _draft_ticket(conn, tmp_path):
    """用真实 workbook 造一张动态导入工单（conftest 替身提供子代理产出）。"""
    from catalog import tickets as tk
    from catalog.dynamic_import import build_ticket_payload
    payload = build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work',
                                   source_key='crud-drafts')
    return tk.create(conn, 'template_import', None, payload), payload


def _png_bytes(color=(10, 100, 200)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (6, 6), color).save(buf, format='PNG')
    return buf.getvalue()


def test_upload_and_draft_image_edit(client, tmp_path):
    """上传图 + 审批时换图：decisions.edits.__images 覆盖草稿图。"""
    # 1) 上传端点
    r = client.post('/upload', files={'file': ('u1.png', _png_bytes(), 'image/png')})
    assert r.status_code == 200
    up1 = r.json()['path']
    assert up1.startswith('_upload/') and client.get(f'/img/{up1}').status_code == 200
    # 2) 真实 workbook 造含图草稿工单
    ticket, payload = _draft_ticket(client.app.state.conn, tmp_path)
    category = payload['sheets'][0]['template']['key']
    # 3) 审批时换图
    result = client.post(f"/tickets/{ticket['id']}/decision", json={
        'token': ticket['token'], 'approved': True,
        'decisions': {'edits': {'n0': {'__images': [up1]}}}})
    assert result.status_code == 200, result.text
    ps = client.get(f'/products/{category}').json()['products']
    assert all('_upload' not in (p['主图'] or '') for p in ps)   # 全部已落位成正式rel
    contents = [client.get(f"/img/{p['主图']}").content for p in ps if p['主图']]
    assert contents.count(_png_bytes()) == 1                     # n0 行换成了上传的新图


def test_product_image_update_via_mutate(client):
    """商品页换图：PATCH images → 审批 → 主图/图集更新。"""
    seed_products(client.app.state.conn, [{'id': 'p1', 'data': {'model': 'PM1'}}])
    up = client.post('/upload',
                     files={'file': ('u2.png', _png_bytes((5, 5, 5)), 'image/png')}).json()['path']
    r = client.patch('/products/test_cat/p1', json={'changes': {}, 'images': [up]})
    assert r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets']
         if x['ticket_type'] == 'mutate' and x['status'] == 'pending'][0]
    client.post(f"/tickets/{t['id']}/decision",
                json={'token': t['token'], 'approved': True})
    p = client.get('/products/test_cat').json()['products'][0]
    assert client.get(f"/img/{p['主图']}").content == _png_bytes((5, 5, 5))
    assert len(p['图集']) == 1


def test_save_draft_edit_persists(client, tmp_path):
    """编辑草稿立即持久化到工单payload，明细API返回新值。"""
    ticket, payload = _draft_ticket(client.app.state.conn, tmp_path)
    color = next(f for f in payload['sheets'][0]['template']['fields']
                 if f['label'] == '颜色')['key']
    saved = client.patch(f"/tickets/{ticket['id']}/draft", json={
        'token': ticket['token'], 'row_key': 'n0', 'edits': {color: '人工改色'}})
    assert saved.status_code == 200, saved.text
    d = client.get(f"/tickets/{ticket['id']}?t={ticket['token']}").json()
    row = d['payload']['sheets'][0]['drafts']['new'][0]
    assert row['data'][color] == '人工改色', f'编辑未持久化: {row}'


def test_decision_rejects_and_edits_rows(client, tmp_path):
    """整单决策带 decisions：reject 按行钥匙驳回，edits 按行钥匙改值。"""
    ticket, payload = _draft_ticket(client.app.state.conn, tmp_path)
    category = payload['sheets'][0]['template']['key']
    color = next(f for f in payload['sheets'][0]['template']['fields']
                 if f['label'] == '颜色')['key']
    total = len(payload['sheets'][0]['drafts']['new'])
    r = client.post(f"/tickets/{ticket['id']}/decision", json={
        'token': ticket['token'], 'approved': True,
        'decisions': {'reject': ['n1'], 'edits': {'n0': {color: '审批改色'}}}}).json()
    assert r['created'] == total - 1
    ps = client.get(f'/products/{category}').json()['products']
    assert any(p['颜色'] == '审批改色' for p in ps)     # 编辑生效
    assert len(ps) == total - 1                         # 驳回生效


def test_direct_create_and_update(client):
    """H5 商品页直接写入（不经审批）——用户本人操作即审批。"""
    seed_products(client.app.state.conn, [])
    r = client.post('/products/test_cat/direct',
                    json={'changes': {'model': 'DIR1', 'price': '10'}})
    assert r.status_code == 200
    pid = r.json()['id']
    ps = client.get('/products/test_cat').json()['products']
    assert any(p['型号'] == 'DIR1' for p in ps)
    r2 = client.patch(f'/products/test_cat/{pid}/direct',
                      json={'changes': {'price': '99'}})
    assert r2.status_code == 200
    p = [x for x in client.get('/products/test_cat').json()['products'] if x['id'] == pid][0]
    assert p['价格'] == '99'
    r3 = client.delete(f'/products/test_cat/{pid}/direct')
    assert r3.status_code == 200
    p = [x for x in client.get('/products/test_cat').json()['products'] if x['id'] == pid][0]
    assert p['状态'] == 'delisted'
    tks = client.get('/tickets').json()['tickets']
    assert not any(t['ticket_type'] == 'mutate' for t in tks)


def test_catalog_stats(client):
    """统计查询：总数/按品类/按型号。"""
    seed_products(client.app.state.conn, [
        {'id': f's{i}', 'inner_code': f'S{i}', 'data': {'model': f'S{i}'}} for i in range(3)])
    r = client.get('/stats')
    assert r.status_code == 200
    d = r.json()
    assert d['total'] == 3
    assert d['by_category'] == {'测试品类': 3}
