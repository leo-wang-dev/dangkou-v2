"""P2 字段体系：DB 加备注 + AI 结构注入 + 后端只校验不翻译。

分工：语义映射是 AI 的活（工具描述里给了字段清单）；
后端 normalize_changes 只做机械过滤——合法键保留，非法键拼进备注，永不 500。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from catalog import db, ingest, tickets
from catalog.main import app
from catalog.storage import LocalStorage
from catalog.templates import TEMPLATES


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.agent, 'parse',
                        lambda cat, p, wd: {'vendor': '厂',
                                            'products': [{'model_no': '8225', 'price': '21.5',
                                                          'remark': '认证：CE'}]})
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = ''
    app.state.storage = LocalStorage(str(tmp_path))
    app.state.callback = None
    return TestClient(app)


# ---------- P2-1 数据库 + 模板 + 导入 ----------

def test_remark_column_added_by_migration():
    """老库（v2.0 表结构，无 remark 列）init_db 自动补列。"""
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE product_razor (id TEXT PRIMARY KEY, inner_code TEXT UNIQUE NOT NULL, "
        "model_no TEXT, description TEXT, color TEXT, size_mm TEXT, giftbox_mm TEXT, "
        "unit_weight_g TEXT, ctn_spec TEXT, price TEXT, "
        "status TEXT NOT NULL DEFAULT 'approved', image_main TEXT, images TEXT DEFAULT '[]', "
        "source_doc INTEGER, created_at TEXT DEFAULT (datetime('now')), "
        "updated_at TEXT DEFAULT (datetime('now')))")
    db.init_db(conn)
    for tbl in ('product_razor', 'product_curler'):
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
        assert 'remark' in cols, tbl


def test_template_has_remark_and_api_exposes_it(client):
    """模板登记备注字段 → /products 的 template.fields 带备注（H5 自动成一列）。"""
    for key in ('razor', 'curler'):
        labels = [l for _, l in TEMPLATES[key].fields]
        assert '备注' in labels
    r = client.get('/products/razor').json()
    assert '备注' in [f['label'] for f in r['template']['fields']]


def test_import_remark_field_persists(client, tmp_path):
    """Excel 导入路径：Sub Agent 产出 remark → 审批后落库。"""
    import time
    f = tmp_path / 'x.xlsx'
    f.write_bytes(b'x')
    doc = client.post('/import', json={'path': str(f), 'category': 'razor'}).json()['doc_id']
    for _ in range(100):
        time.sleep(0.05)
        if client.get(f'/import/{doc}').json()['status'] != 'parsing':
            break
    t = client.get('/tickets').json()['tickets'][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    p = client.get('/products/razor').json()['products'][0]
    assert p['备注'] == '认证：CE'


# ---------- P2-2 normalize：合法保留 / 非法进备注 / 不翻译 ----------

def test_normalize_keeps_legal_labels_and_cols():
    t = TEMPLATES['curler']
    norm, to_remark = tickets.normalize_changes(
        t, {'ITEM.NO 型号': 'KS-0376', '价格': '28', 'voltage': '110-240V'})
    assert norm == {'ITEM.NO 型号': 'KS-0376', '价格': '28', '电压': '110-240V'}
    assert to_remark == []


def test_normalize_illegal_keys_overflow_to_remark():
    """非法键不翻译（那是AI的活），值原样拼进备注——信息不丢。"""
    t = TEMPLATES['curler']
    norm, to_remark = tickets.normalize_changes(
        t, {'产品型号': 'KS-0376', '额定电压': '110-240V', '认证': 'CE', '价格': '28'})
    assert norm['价格'] == '28'
    assert '产品型号：KS-0376' in norm['备注']
    assert '额定电压：110-240V' in norm['备注']
    assert '认证：CE' in norm['备注']
    assert set(to_remark) == {'产品型号', '额定电压', '认证'}


def test_normalize_skips_empty_and_merges_existing_remark():
    t = TEMPLATES['curler']
    norm, _ = tickets.normalize_changes(
        t, {'价格': '', '备注': '已有备注', '随便': 'x'})
    assert norm == {'备注': '已有备注｜随便：x'}


def test_create_mutate_all_illegal_returns_400_with_field_list(client):
    """B 层：全非法键 → 400 + 字段清单原文返回，AI 自纠重发。"""
    r = client.post('/products/curler', json={'changes': {'随便什么': 'x', '另一个': 'y'}})
    assert r.status_code == 400
    detail = r.json()['detail']
    assert 'ITEM.NO' in detail and '备注' in detail


def test_create_mutate_legal_labels_flow_to_product(client):
    """AI 按清单发字段（含清单外进备注）→ 建单 200 → 审批落库。"""
    r = client.post('/products/curler', json={'changes': {
        'ITEM.NO 型号': '8226', '价格': '21.5', '备注': '工作温度：160-220℃'}})
    assert r.status_code == 200
    tid = r.json()['ticket_id']
    t = [x for x in client.get('/tickets').json()['tickets'] if x['id'] == tid][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    p = client.get('/products/curler').json()['products'][0]
    assert p['ITEM.NO 型号'] == '8226'
    assert p['备注'] == '工作温度：160-220℃'


def test_legacy_dirty_ticket_decides_without_500():
    """C 层保命：存量脏工单（#5/#6 同款全自由字段）决策不炸，内容进备注。"""
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    tk = tickets.create(conn, 'mutate', 'curler',
                        {'kind': 'mutate', 'action': 'create', 'product_id': None,
                         'changes': {'产品型号': 'KS-0376', '报价': '28',
                                     '额定电压': '110-240V', '认证': 'CE'}})
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (tk['id'],)).fetchone()
    r = tickets.decide(conn, tk['id'], row['token'], True)   # 之前这里 OperationalError
    assert r['mutated'] == 'create'
    p = conn.execute('SELECT * FROM product_curler').fetchone()
    assert '产品型号：KS-0376' in p['remark']
    assert '报价：28' in p['remark']


def test_update_changes_and_images_both_applied(tmp_path):
    """顺带修的bug：update 同带字段+图片，字段不再被 images 早退吞掉。"""
    import base64
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    rel = st.save('curler', 'p1', 'main.png', png)
    conn.execute("INSERT INTO product_curler(id, inner_code, item_no, price, image_main) "
                 "VALUES('p1','KS-AAAAAAAA','8226','21.5',?)", (rel,))
    conn.commit()
    tk = tickets.create(conn, 'mutate', 'curler',
                        {'kind': 'mutate', 'action': 'update', 'product_id': 'p1',
                         'changes': {'价格': '23'}, 'images': [rel]})
    row = conn.execute('SELECT token FROM approval_ticket WHERE status=?', ('pending',)).fetchone()
    tid = conn.execute('SELECT id FROM approval_ticket WHERE token=?', (row['token'],)).fetchone()['id']
    r = tickets.decide(conn, tid, row['token'], True)
    assert r['mutated'] == 'update'
    assert conn.execute('SELECT price FROM product_curler WHERE id=?', ('p1',)).fetchone()['price'] == '23'


# ---------- P5 下架工单带商品信息 ----------

def test_delete_ticket_carries_before_snapshot(client, tmp_path):
    """下架工单带 before（型号/主图），审批页能核对批的是哪个货。"""
    import time
    f = tmp_path / 'x.xlsx'
    f.write_bytes(b'x')
    doc = client.post('/import', json={'path': str(f), 'category': 'razor'}).json()['doc_id']
    for _ in range(100):
        time.sleep(0.05)
        if client.get(f'/import/{doc}').json()['status'] != 'parsing':
            break
    t = client.get('/tickets').json()['tickets'][0]
    client.post(f"/tickets/{t['id']}/decision", json={'token': t['token'], 'approved': True})
    pid = client.get('/products/razor').json()['products'][0]['id']
    r = client.delete(f'/products/razor/{pid}')
    tid = r.json()['ticket_id']
    payload = client.get(f'/tickets/{tid}').json()['payload']
    assert payload['action'] == 'delete'
    assert payload['before']['产品型号'] == '8225'
    assert payload['before']['主图'] is not None or payload['before']['主图'] == ''


def test_h5_delete_card_renders_before():
    """下架卡片必须用 before 渲染（图+型号），不再裸 ID。"""
    import os as _os
    s = open(_os.path.join(_os.path.dirname(__file__), '..', 'static', 'index.html'),
             encoding='utf-8').read()
    i = s.find("act === 'delete'")
    seg = s[i:i + 900]
    assert i >= 0 and 'p.before' in seg and '主图' in seg
