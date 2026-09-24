"""动态分类的子代理（Claude/Docker）商品解析：产出形状、回落、提示词。"""
import json

import pytest

from catalog import agent, db, dynamic_import
from tests.test_workbook_templates import blowdryer_fixture


@pytest.fixture
def conn():
    value = __import__('sqlite3').connect(':memory:', check_same_thread=False)
    value.row_factory = __import__('sqlite3').Row
    db.init_db(value)
    yield value
    value.close()

TEMPLATE = {'key': 'cat_x', 'name': '华悦电器-电吹风', 'storage': 'dynamic', 'fields': [
    {'key': 'model', 'label': '型号', 'role': 'model', 'type': 'text'},
    {'key': 'field_p', 'label': '功率', 'role': 'spec', 'type': 'text'},
    {'key': 'note_f', 'label': '备注', 'role': 'note', 'type': 'text'},
    {'key': 'img_f', 'label': '图片', 'role': 'image', 'type': 'image'},
]}


def test_dynamic_prompt_carries_fields_and_messy_sheet_rules():
    p = agent.build_dynamic_prompt(TEMPLATE, '/input/source.xlsx', '/work/products.json', 'Sheet1')
    assert '型号(model) ← 型号字段' in p
    assert '功率(field_p)' in p
    assert '只处理工作表「Sheet1」' in p
    # 乱表三规则：跨行合并、图片行不是商品、值逐字
    assert '一个逻辑商品可能占多个物理行' in p
    assert '只有图片没有数据的行不是商品' in p
    assert '禁止编造、改写、翻译、换算单位' in p
    assert 'DONE N' in p


def test_agent_rows_shape_and_image_main_prepend(tmp_path, monkeypatch):
    (tmp_path / 'a.png').write_bytes(b'A')
    (tmp_path / 'b.png').write_bytes(b'B')
    monkeypatch.setattr(agent, 'parse_dynamic', lambda tpl, xlsx, wd, sheet='': {
        'vendor': '华悦',
        'products': [
            {'model': 'T821', 'field_p': '2000W', 'note_f': '', 'image_main': 'a.png',
             'images': ['a.png', 'b.png'], 'image_count': 2},
            {'model': '', 'field_p': '', 'note_f': '', 'images': []},   # 空行应被丢弃
        ]})
    rows = dynamic_import._agent_rows(TEMPLATE, '/tmp/fake.xlsx', tmp_path)
    assert rows is not None and len(rows) == 1
    row = rows[0]
    assert row['data'] == {'model': 'T821', 'field_p': '2000W', 'note_f': ''}
    assert row['images'] == ['a.png', 'b.png'] and row['image_main'] == 'a.png'
    assert len(row['row_fingerprint']) == 64
    assert set(row) >= {'data', 'images', 'source_row', 'row_fingerprint'}


def test_agent_failure_falls_back(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('docker 不可用')
    monkeypatch.setattr(agent, 'parse_dynamic', boom)
    assert dynamic_import._agent_rows(TEMPLATE, '/tmp/fake.xlsx', '/tmp') is None
    monkeypatch.setattr(agent, 'parse_dynamic', lambda *a, **kw: {'products': []})
    assert dynamic_import._agent_rows(TEMPLATE, '/tmp/fake.xlsx', '/tmp') is None


def test_legacy_payload_prefers_agent_rows(conn, tmp_path, monkeypatch):
    """一阶段旧路径：子代理产出优先，直接进 drafts。"""
    from catalog.dynamic_import import build_ticket_payload
    monkeypatch.setattr(agent, 'parse_dynamic', lambda tpl, xlsx, wd, sheet='': {
        'vendor': None,
        'products': [{'model': 'T821', 'field_p': '2000W', 'note_f': '',
                      'image_main': '', 'images': [], 'image_count': 0}]})
    payload = build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work',
                                   source_key='vendor-a', doc_id=1)
    sheet = payload['sheets'][0]
    models = [d['data'].get('model') for d in sheet['drafts']['new']]
    assert 'T821' in models


def test_legacy_payload_agent_down_falls_back_to_discovery(conn, tmp_path, monkeypatch):
    from catalog.dynamic_import import build_ticket_payload
    monkeypatch.setattr(agent, 'parse_dynamic', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('down')))
    payload = build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work',
                                   source_key='vendor-a', doc_id=1)
    assert len(payload['sheets'][0]['drafts']['new']) >= 1
