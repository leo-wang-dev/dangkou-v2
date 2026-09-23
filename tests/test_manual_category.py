"""手工建分类（不经 Excel）：页面/bot 提交 → 模板工单 → 批准后空分类上列表。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from catalog import db
from catalog.main import app
from catalog.storage import LocalStorage


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


def _auth():
    return {'X-Service-Token': 'T0KEN'}


def test_manual_category_full_flow(client):
    r = client.post('/categories', headers=_auth(),
                    json={'name': '吹风机现货',
                          'fields': [{'label': '型号'}, {'label': '成本', 'visibility': 'internal'}]})
    assert r.status_code == 200
    ticket = r.json()
    assert ticket['fields'] == 2

    names = [c['name'] for c in client.get('/categories', headers=_auth()).json()['categories']]
    assert '吹风机现货' not in names  # 批准前不上列表

    d = client.post(f"/tickets/{ticket['ticket_id']}/decision",
                    json={'token': ticket['token'], 'approved': True})
    assert d.status_code == 200

    cats = client.get('/categories', headers=_auth()).json()['categories']
    entry = next(c for c in cats if c['name'] == '吹风机现货')
    assert entry['storage'] == 'dynamic'  # 空动态分类也上列表
    labels = [f['label'] for f in entry['fields']]
    assert labels == ['型号', '成本']
    by_label = {f['label']: f for f in entry['fields']}
    assert by_label['成本']['visibility'] == 'internal'


def test_manual_category_validation(client):
    assert client.post('/categories', headers=_auth(),
                       json={'name': '  ', 'fields': [{'label': '型号'}]}).status_code == 400
    assert client.post('/categories', headers=_auth(),
                       json={'name': 'X', 'fields': []}).status_code == 400
    r = client.post('/categories', headers=_auth(),
                    json={'name': 'X', 'fields': [{'label': 'a'}, {'label': 'a'}]})
    assert r.status_code == 400 and '重复' in r.json()['detail']


def test_duplicate_name_conflicts(client):
    assert client.post('/categories', headers=_auth(),
                       json={'name': '剃须刀现货', 'fields': [{'label': '型号'}]}).status_code == 200
    r = client.post('/categories', headers=_auth(),
                    json={'name': '剃须刀现货', 'fields': [{'label': '型号'}]})
    assert r.status_code == 409


def test_ai_mapping_and_inference_fallback(client, monkeypatch):
    """AI 不可用时模板/商品阶段回落代码推断，导入不硬失败。"""
    from catalog import ai_extract
    monkeypatch.setattr(ai_extract, '_enabled', lambda: False)
    assert ai_extract.infer_field_attributes([{'title': 'S', 'fields': [{'label': '型号'}]}]) == {}
    assert ai_extract.map_rows({'fields': [{'key': 'model', 'label': '型号'}],
                                'rows': [{'data': {'model': 'A1'}}]},
                               {'fields': [{'key': 'm', 'label': '型号', 'role': 'spec'}]}) is None


def test_ai_extract_parsing(monkeypatch):
    from catalog import ai_extract
    sheets = [{'title': 'Sheet1', 'fields': [
        {'key': 'model', 'label': '产品型号', 'type': 'text', 'role': 'spec'},
        {'key': 'field_x', 'label': '报价', 'type': 'text', 'role': 'spec'}]}]
    reply = ('```json\n[{"sheet":"Sheet1","label":"产品型号","type":"text","role":"model",'
             '"visibility":"public","searchable":true},'
             '{"sheet":"Sheet1","label":"报价","type":"number","role":"price",'
             '"visibility":"public","searchable":false}]')
    monkeypatch.setattr(ai_extract.llm, 'chat_text', lambda *a, **kw: reply)
    attrs = ai_extract.infer_field_attributes(sheets)
    assert attrs['Sheet1']['产品型号']['role'] == 'model'
    assert attrs['Sheet1']['报价']['type'] == 'number'

    discovered = {'fields': [{'key': 'src1', 'label': '货号'}, {'key': 'src2', 'label': '单价'}],
                  'rows': [{'data': {'src1': 'A1', 'src2': '9.9'}, 'images': []}]}
    template = {'fields': [{'key': 'm', 'label': '型号', 'role': 'model'},
                           {'key': 'p', 'label': '价格', 'role': 'price'}]}
    mapping = '[{"row":0,"values":{"型号":"A1","价格":"9.9"}}]'
    monkeypatch.setattr(ai_extract.llm, 'chat_text', lambda *a, **kw: mapping)
    out = ai_extract.map_rows(discovered, template)
    assert out and out[0]['data'] == {'m': 'A1', 'p': '9.9'}
    assert out[0]['images'] == []  # 行结构原样保留
