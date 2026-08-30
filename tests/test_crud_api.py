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


def test_ticket_detail_and_row_level_reject(client):
    _import_and_approve(client)   # 先导一批（1个）
    import tempfile, time
    f = tempfile.mktemp(suffix='.xlsx'); open(f, 'wb').write(b'x')
    import json as _j
    # 手动建一张含两行新草稿的工单（带 _rid）
    from catalog import tickets as tk
    tk.create(client.app.state.conn, 'import', 'razor',
              {'kind': 'import', 'work_dir': None,
               'drafts': {'new': [{'model_no': 'R1', 'price': '1', '_rid': 'n0'},
                                  {'model_no': 'R2', 'price': '2', '_rid': 'n1'}],
                          'update': [], 'delist': []}})
    d = client.get('/tickets/2').json()
    assert d['payload']['drafts']['new'][0]['_rid'] == 'n0'
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == 2][0]
    # 驳回 n1 这一行，整单通过
    r = client.post('/tickets/2/decision', json={
        'token': t['token'], 'approved': True, 'decisions': {'reject': ['n1']}}).json()
    assert r['created'] == 1
    models = [p['产品型号'] for p in client.get('/products/razor').json()['products']]
    assert 'R1' in models and 'R2' not in models


def test_decision_with_row_edits(client):
    """审核时可编辑：decisions.edits 按 _rid 改草稿字段后再落库。"""
    from catalog import tickets as tk
    tk.create(client.app.state.conn, 'import', 'razor',
              {'kind': 'import', 'work_dir': None,
               'drafts': {'new': [{'model_no': 'E1', 'price': '1', '_rid': 'n0'},
                                  {'model_no': 'E2', 'price': '2', '_rid': 'n1'}],
                          'update': [], 'delist': []}})
    t = [x for x in client.get('/tickets').json()['tickets']
         if x['ticket_type'] == 'import' and x['status'] == 'pending'][-1]
    r = client.post(f"/tickets/{t['id']}/decision", json={
        'token': t['token'], 'approved': True,
        'decisions': {'reject': ['n1'],
                      'edits': {'n0': {'price': '99', 'color': '黑'}}}}).json()
    assert r['created'] == 1
    ps = client.get('/products/razor').json()['products']
    e1 = [p for p in ps if p['产品型号'] == 'E1'][0]
    assert e1['报价'] == '99' and e1['颜色'] == '黑'   # 编辑生效
    assert not [p for p in ps if p['产品型号'] == 'E2']  # 驳回生效


def test_import_returns_est_sec_floor(client, tmp_path):
    """预估公式：max(120, MB×30)——小文件吃保底。"""
    import tempfile
    f = tempfile.mktemp(suffix='.xlsx')
    open(f, 'wb').write(b'x' * (2 * 1048576))   # 2MB
    r = client.post('/import', json={'path': f, 'category': 'razor'}).json()
    assert r['est_sec'] == 120                    # 2×30=60 < 保底120


def test_ticket_detail_lists_all_preview_images(client):
    from catalog import tickets as tk
    tk.create(client.app.state.conn, 'import', 'razor',
              {'kind': 'import', 'work_dir': '/tmp',
               'drafts': {'new': [{'model_no': 'P1', 'image_main': 'a.png',
                                   'images': ['a.png', 'b.png'], '_rid': 'n0'}],
                          'update': [], 'delist': []}})
    tid = [t['id'] for t in client.get('/tickets').json()['tickets']
           if t['ticket_type'] == 'import'][0]
    d = client.get(f'/tickets/{tid}').json()
    row = d['payload']['drafts']['new'][0]
    assert len(row['_imgs']) == 2                  # 全部图都有预览地址
    assert f'/ticketimg/{tid}/a.png' in row['_imgs'][0]


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
    # 2) 建含图的草稿工单
    from catalog import tickets as tk
    wd = tmp_path / 'wd'; wd.mkdir()
    (wd / 'a.png').write_bytes(_png_bytes((1, 1, 1)))
    tk.create(client.app.state.conn, 'import', 'razor',
              {'kind': 'import', 'work_dir': str(wd),
               'drafts': {'new': [{'model_no': 'IM1', 'image_main': 'a.png',
                                   'images': ['a.png'], '_rid': 'n0'}],
                          'update': [], 'delist': []}})
    tid = [t['id'] for t in client.get('/tickets').json()['tickets']
           if t['ticket_type'] == 'import'][0]
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    # 3) 审批时换图
    client.post(f'/tickets/{tid}/decision', json={
        'token': t['token'], 'approved': True,
        'decisions': {'edits': {'n0': {'__images': [up1]}}}})
    p = client.get('/products/razor').json()['products'][0]
    assert '_upload' not in (p['主图'] or '')      # 已落位成正式rel
    assert client.get(f"/img/{p['主图']}").status_code == 200
    r2 = client.get(f"/img/{p['主图']}").content
    assert r2 == _png_bytes()                      # 内容是上传的新图


def test_product_image_update_via_mutate(client):
    """商品页换图：PATCH images → 审批 → 主图/图集更新且向量重嵌。"""
    from catalog import tickets as tk
    tk.create(client.app.state.conn, 'mutate', 'razor',
              {'kind': 'mutate', 'action': 'create', 'product_id': None,
               'changes': {'model_no': 'PM1'}})
    t0 = [x for x in client.get('/tickets').json()['tickets']
          if x['ticket_type'] == 'mutate'][0]
    client.post(f"/tickets/{t0['id']}/decision",
                json={'token': t0['token'], 'approved': True})
    pid = client.get('/products/razor').json()['products'][0]['id']
    up = client.post('/upload',
                     files={'file': ('u2.png', _png_bytes((5, 5, 5)), 'image/png')}).json()['path']
    r = client.patch(f'/products/razor/{pid}', json={'changes': {}, 'images': [up]})
    assert r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets']
         if x['ticket_type'] == 'mutate' and x['status'] == 'pending'][0]
    client.post(f"/tickets/{t['id']}/decision",
                json={'token': t['token'], 'approved': True})
    p = client.get('/products/razor').json()['products'][0]
    assert client.get(f"/img/{p['主图']}").content == _png_bytes((5, 5, 5))
    assert len(p['图集']) == 1


def test_row_level_approve(client):
    """单行通过：只落库指定行，工单保持存活。"""
    from catalog import tickets as tk
    tk.create(client.app.state.conn, 'import', 'razor',
              {'kind': 'import', 'work_dir': None,
               'drafts': {'new': [{'model_no': 'SA1', '_rid': 'n0'},
                                  {'model_no': 'SA2', '_rid': 'n1'},
                                  {'model_no': 'SA3', '_rid': 'n2'}],
                          'update': [], 'delist': []}})
    tid = [t['id'] for t in client.get('/tickets').json()['tickets']
           if t['ticket_type'] == 'import'][0]
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    # 单行通过 SA1
    r = client.post(f'/tickets/{tid}/row', json={
        'token': t['token'], 'row_key': 'n0', 'approved': True})
    assert r.status_code == 200
    models = [p['产品型号'] for p in client.get('/products/razor').json()['products']]
    assert 'SA1' in models and 'SA2' not in models and 'SA3' not in models
    # 工单还活着（SA2/SA3 待处理）
    t2 = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    assert t2['status'] == 'pending'
    # 再单行通过 SA2
    r2 = client.post(f'/tickets/{tid}/row', json={
        'token': t['token'], 'row_key': 'n1', 'approved': True})
    assert r2.status_code == 200
    models = [p['产品型号'] for p in client.get('/products/razor').json()['products']]
    assert 'SA2' in models
    # 单行驳回 SA3
    r3 = client.post(f'/tickets/{tid}/row', json={
        'token': t['token'], 'row_key': 'n2', 'approved': False})
    assert r3.status_code == 200
    # 全部处理完，工单应自动关闭
    t3 = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    assert t3['status'] == 'approved'
    models = [p['产品型号'] for p in client.get('/products/razor').json()['products']]
    assert 'SA3' not in models
