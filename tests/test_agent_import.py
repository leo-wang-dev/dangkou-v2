"""动态分类的子代理（Claude/Docker）商品解析与模板表头发现：产出形状、失败报错、提示词。"""
import json

import pytest

from catalog import agent, ai_extract, db, dynamic_import
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


def test_agent_failure_returns_none_and_import_raises(conn, tmp_path, monkeypatch):
    """子代理失败/产出空 → _agent_rows 返回 None，导入直接报错（删A 后无代码回落）。"""
    def boom(*a, **kw):
        raise RuntimeError('docker 不可用')
    monkeypatch.setattr(agent, 'parse_dynamic', boom)
    assert dynamic_import._agent_rows(TEMPLATE, '/tmp/fake.xlsx', '/tmp') is None
    monkeypatch.setattr(agent, 'parse_dynamic', lambda *a, **kw: {'products': []})
    assert dynamic_import._agent_rows(TEMPLATE, '/tmp/fake.xlsx', '/tmp') is None
    with pytest.raises(ValueError, match='解析服务暂不可用'):
        dynamic_import.build_ticket_payload(
            conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='vendor-a', doc_id=1)


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


def test_legacy_payload_agent_down_raises(conn, tmp_path, monkeypatch):
    """一阶段旧路径：子代理失败即报错，不再回落 discover 行直落。"""
    from catalog.dynamic_import import build_ticket_payload
    monkeypatch.setattr(agent, 'parse_dynamic', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('down')))
    with pytest.raises(ValueError, match='解析服务暂不可用'):
        build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work',
                             source_key='vendor-a', doc_id=1)
    monkeypatch.setattr(agent, 'parse_dynamic', lambda *a, **kw: {'vendor': None, 'products': []})
    with pytest.raises(ValueError, match='解析服务暂不可用'):
        build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work2',
                             source_key='vendor-a', doc_id=1)


def test_template_discovery_prompt_rules():
    p = agent.build_template_discovery_prompt('/input/source.xlsx', '/work/template.json', 'Sheet1')
    assert '只分析工作表「Sheet1」' in p
    # 多级表头找齐
    assert '多级表头' in p and '合并值只在左上角' in p
    # 无标头图片列=图片列
    assert '没有表头文字、但该列锚定了内嵌图片的列=图片列' in p
    # 空列跳过、不发明列、乱表选主商品表
    assert '空列直接跳过' in p and '禁止发明表里不存在的列' in p
    assert '主商品表' in p
    # 角色白名单 + 可见性口径
    assert 'model=该表唯一的型号/货号/品名列' in p
    assert '价格、成本、库存、供应商类内部信息一律 internal' in p
    assert '"sheets"' in p and 'DONE N' in p


def test_template_payload_prefers_agent_discovery(conn, tmp_path, monkeypatch):
    """agent 模板发现优先：多级表头拼 label、无标头图片列保留、header_row 用 agent 的。"""
    qwen_ran = []

    def spy_attrs(sheets):
        qwen_ran.append(sheets)
        return {}

    monkeypatch.setattr(ai_extract, 'infer_field_attributes', spy_attrs)
    monkeypatch.setattr(agent, 'parse_dynamic_template', lambda xlsx, wd, sheet='': {
        'sheets': [
            {'title': '吹风机', 'header_row': 3, 'columns': [
                {'col': 1, 'label': 'ITEM.NO 型号', 'role': 'model', 'type': 'text',
                 'visibility': 'public', 'searchable': True},
                # 两级表头下图片列没有表头文字：靠图片锚定识别
                {'col': 2, 'label': '', 'role': 'image', 'type': 'image',
                 'visibility': 'public', 'searchable': False},
                {'col': 3, 'label': '拿货价', 'role': 'price', 'type': 'money',
                 'visibility': 'internal', 'searchable': False},
                # 无表头又无图片的空列：跳过
                {'col': 4, 'label': '', 'role': 'spec', 'type': 'text',
                 'visibility': 'public', 'searchable': False},
            ]},
            # 乱表 Sheet：agent 只报主商品表的列
            {'title': '汇总', 'header_row': 2, 'columns': [
                {'col': 1, 'label': '货号', 'role': 'model', 'type': 'text',
                 'visibility': 'public', 'searchable': True},
            ]},
        ]})
    payload = dynamic_import.build_template_payload(
        conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='vendor-a', doc_id=1)
    assert [s['template']['name'] for s in payload['sheets']] == ['吹风机', '汇总']
    sheet = payload['sheets'][0]
    assert sheet['header_row'] == 3 and sheet['image_count'] == 0
    fields = sheet['template']['fields']
    assert [(f['label'], f['role'], f['type'], f['visibility']) for f in fields] == [
        ('ITEM.NO 型号', 'model', 'text', 'public'),
        ('图片', 'image', 'image', 'public'),
        ('拿货价', 'price', 'money', 'internal'),
    ]
    assert fields[0]['key'] == 'model' and fields[1]['key'] == 'image'
    assert fields[2]['key'].startswith('field_')
    # agent 路径不跑 qwen 属性推断（属性以 agent 为准）
    assert qwen_ran == []


def test_template_payload_agent_invalid_values_fall_back_to_code_roles(conn, tmp_path, monkeypatch):
    """agent 给的 role/type/visibility 越界时按 label 回落代码推断，不照单全收。"""
    monkeypatch.setattr(ai_extract, 'infer_field_attributes', lambda sheets: {})
    monkeypatch.setattr(agent, 'parse_dynamic_template', lambda xlsx, wd, sheet='': {
        'sheets': [{'title': '吹风机', 'header_row': '2', 'columns': [
            {'col': 1, 'label': '产品型号', 'role': 'bogus', 'type': 'video',
             'visibility': 'secret', 'searchable': 'yes'},
            {'col': 9, 'label': '成本', 'role': 'cost', 'type': 'money',
             'visibility': 'internal', 'searchable': False},
            {'col': None, 'label': '坏行', 'role': 'note', 'type': 'text',
             'visibility': 'internal', 'searchable': False},
        ]}]})
    payload = dynamic_import.build_template_payload(
        conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='vendor-a', doc_id=1)
    fields = payload['sheets'][0]['template']['fields']
    assert [(f['label'], f['role']) for f in fields] == [('产品型号', 'model'), ('成本', 'cost')]
    assert fields[0]['type'] == 'text' and fields[0]['visibility'] == 'public'
    assert fields[0]['searchable'] is True   # 型号可搜
    assert payload['sheets'][0]['header_row'] == 2   # 字符串行号被容错成 int


def test_template_payload_agent_failure_falls_back(conn, tmp_path, monkeypatch):
    """agent 异常 / 0 sheet → 回落 discover_workbook 现状。"""
    monkeypatch.setattr(ai_extract, 'infer_field_attributes', lambda sheets: {})
    monkeypatch.setattr(agent, 'parse_dynamic_template',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('docker 不可用')))
    payload = dynamic_import.build_template_payload(
        conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='vendor-a', doc_id=1)
    sheet = payload['sheets'][0]
    assert sheet['header_row'] == 2   # discover_workbook 的结果
    assert sheet['template']['name'] == '吹风机'
    assert any(f['role'] == 'model' for f in sheet['template']['fields'])

    monkeypatch.setattr(agent, 'parse_dynamic_template', lambda *a, **kw: {'sheets': []})
    payload2 = dynamic_import.build_template_payload(
        conn, blowdryer_fixture(tmp_path), tmp_path / 'work2', source_key='vendor-b', doc_id=2)
    assert payload2['sheets'][0]['header_row'] == 2

    # 结构不合法（缺 sheets）同样视作失败回落
    monkeypatch.setattr(agent, 'parse_dynamic_template', lambda *a, **kw: {'sheets': 'nope'})
    payload3 = dynamic_import.build_template_payload(
        conn, blowdryer_fixture(tmp_path), tmp_path / 'work3', source_key='vendor-c', doc_id=3)
    assert payload3['sheets'][0]['template']['name'] == '吹风机'


@pytest.mark.real_agent
def test_parse_dynamic_template_validates_container_output(tmp_path, monkeypatch):
    """parse_dynamic_template 对容器产物做结构校验：非对象数组报错（调用方回落）。

    real_agent 标记只是绕开 conftest 的离线替身直连真实函数；容器本身用
    _run_container 假替身，不会碰 Docker。
    """
    bad = tmp_path / 'template.json'

    def fake_run(prompt, xlsx, wd, *, out_name='products.json'):
        bad.write_text(json.dumps({'sheets': [{'title': 'S'}]} if out_name == 'template.json'
                                  else {'products': []}), encoding='utf-8')
        return json.loads(bad.read_text(encoding='utf-8'))

    monkeypatch.setattr(agent, '_run_container', fake_run)
    ok = agent.parse_dynamic_template(str(tmp_path / 'in.xlsx'), str(tmp_path))
    assert ok == {'sheets': [{'title': 'S'}]}

    def bad_run(prompt, xlsx, wd, *, out_name='products.json'):
        return {'sheets': 'nope'}

    monkeypatch.setattr(agent, '_run_container', bad_run)
    with pytest.raises(RuntimeError, match='sheets 对象数组'):
        agent.parse_dynamic_template(str(tmp_path / 'in.xlsx'), str(tmp_path))
