"""Product supplier identity and incremental import regressions (file SQLite)."""
import sqlite3
import pytest
from catalog import db, dynamic_catalog as dc, dynamic_import as di, customer_catalog
from tests.test_dynamic_catalog import hairdryer_draft

@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / 'supplier.sqlite'))
    db.init_db(c)
    yield c
    c.close()

def seed(c, key='hairdryer', supplier='厂甲', internal=False):
    tpl = hairdryer_draft()
    tpl.update(key=key, supplier=supplier)
    if internal:
        tpl['fields'][0]['visibility'] = 'internal'
    dc.approve_template(c, tpl)

def test_template_supplier_and_image_type_survive_approval(conn):
    seed(conn)
    assert dc.get_template(conn, 'hairdryer')['supplier'] == '厂甲'
    tpl = dc.get_template(conn, 'hairdryer')
    tpl['fields'].append({'key':'extra_photo', 'label':'补充图片', 'type':'image'})
    updated = dc.approve_template(conn, tpl, expected_version=1)
    assert updated['supplier'] == '厂甲'
    assert updated['fields'][-1]['role'] == 'image'

def test_product_supplier_is_persistent_and_public_model_respects_visibility(conn):
    seed(conn, internal=True)
    dc.upsert_approved_products(conn, 'hairdryer', [
        {'id':'a', 'supplier':'厂甲', 'data':{'model':'SECRET-1'}, 'cs_visible':1},
        {'id':'b', 'supplier':'厂乙', 'data':{'model':'SECRET-1'}, 'cs_visible':1}])
    conn.commit()
    rows = dc.list_products(conn, 'hairdryer')
    assert {p['supplier'] for p in rows} == {'厂甲', '厂乙'}
    public = customer_catalog.local_catalog(conn)['products']
    assert 'SECRET-1' not in str(public)
    assert {p['supplier'] for p in public} == {'厂甲', '厂乙'}
    dc.upsert_approved_products(conn, 'hairdryer', [{**rows[1], 'data':{'model':'UPDATED'}}])
    assert dc.list_products(conn, 'hairdryer')[1]['supplier'] == '厂乙'

def draft(model='M1', supplier='厂甲', color='红', fp='new'):
    return {'supplier':supplier, 'data':{'model':model, 'color':color}, 'row_fingerprint':fp}

def test_same_model_different_suppliers_and_variants_do_not_overwrite():
    old = [{**draft(fp='old'), 'id':'a'}]
    result = di._classify_rows(old, [draft(supplier='厂乙')], ['model'])
    assert len(result['new']) == 1 and result['update'] == []
    assert result['delist'] == []
    result = di._classify_rows(old, [draft(color='蓝')], ['model'])
    assert len(result['new']) == 1 and result['update'] == []

def test_filename_reupload_matches_and_stale_approval_checks_actual_records(conn, monkeypatch, tmp_path):
    seed(conn)
    dc.upsert_approved_products(conn, 'hairdryer', [{**draft(fp='old'), 'id':'a', 'cs_visible':1}], source_key='old.xlsx')
    incoming = [draft()]
    monkeypatch.setattr(di, '_agent_rows', lambda *a, **k: incoming)
    payload = di.build_product_payload(conn, 'renamed.xlsx', tmp_path, source_key='renamed.xlsx', template_doc_id=None, category_key='hairdryer')
    assert payload['sheets'][0]['drafts']['update'][0][0]['id'] == 'a'
    conn.execute("UPDATE product_dynamic SET cs_visible=0 WHERE id='a'")
    from catalog.tickets import TicketConflict
    with pytest.raises(TicketConflict):
        di._apply_product_payload(conn, payload)

@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from catalog.api import register_routes
    from catalog.storage import LocalStorage
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'test-token'
    app.state.storage = LocalStorage(str(tmp_path / 'images'))
    app.state.callback = None
    register_routes(app)
    monkeypatch.delenv('CATALOG_NOTIFY_TOKEN', raising=False)
    with TestClient(app) as value:
        yield value

AUTH = {'X-Service-Token':'test-token'}

def test_http_supplier_statistics_and_quote_details(conn, client, monkeypatch):
    from catalog import notify
    notices = []
    monkeypatch.setattr(notify, 'push_file', lambda message, path, **kw: notices.append(message))
    from tests.conftest import DYNAMIC_FIELDS
    for key in ('one', 'two'):
        dc.approve_template(conn, {'key':key, 'name':key, 'supplier':'分类默认', 'fields':DYNAMIC_FIELDS})
    conn.commit()
    ids = []
    for category, supplier, visible in [('one','厂甲',1), ('one','厂乙',1), ('two','厂甲',1), ('two','厂甲',0)]:
        response = client.post(f'/products/{category}/direct', headers=AUTH, json={'changes':{'supplier':supplier, 'model':'M1', 'price':'65', 'ctn':'40件/箱', 'cs_visible':str(visible)}})
        assert response.status_code == 200
        ids.append(response.json()['id'])
    products = client.get('/products/one', headers=AUTH).json()['products']
    assert {p['supplier'] for p in products} == {'厂甲','厂乙'}
    groups = {p['supplier']:p for p in client.get('/stats/supplier', headers=AUTH).json()['suppliers']}
    assert groups['厂甲']['visible'] == 2
    assert groups['厂甲']['total'] == 3
    assert groups['厂乙']['visible'] == 1
    response = client.post('/quote', headers=AUTH, json={'items':[{'category':'one','product_id':ids[1], 'quantity':50}], 'price_adjustment_pct':3})
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert (item['supplier'], item['requested_quantity'], item['quoted_quantity'], item['unit_price'], item['amount']) == ('厂乙',50,80,66.95,5356)
    assert '50' in notices[0] and '80' in notices[0] and '整箱' in notices[0]
    response = client.patch(f'/products/one/{ids[1]}/direct', headers=AUTH, json={'changes':{'supplier':'厂丙'}})
    assert response.status_code == 200
    assert next(p for p in client.get('/products/one',headers=AUTH).json()['products'] if p['id']==ids[1])['supplier'] == '厂丙'

def test_customer_supplier_disambiguation(conn):
    from catalog.csbot import CsBot
    seed(conn)
    dc.upsert_approved_products(conn, 'hairdryer', [{'id':x, 'supplier':supplier, 'data':{'model':'M1'}, 'cs_visible':1} for x,supplier in [('a','厂甲'),('b','厂乙')]])
    products = customer_catalog.local_catalog(conn)['products']
    assert len(CsBot._matching_products('M1', products)) == 2
    assert [p['id'] for p in CsBot._matching_products('厂乙 M1', products)] == ['b']
    from catalog.photo_inquiry import local_candidates
    assert {p['supplier'] for p in local_candidates(conn,[{'型号':'M1'}],b'')} == {'厂甲','厂乙'}

def test_migration_backfills_supplier_once(conn):
    seed(conn)
    dc.upsert_approved_products(conn, 'hairdryer', [{'id':'old','data':{'model':'M1'}}])
    conn.execute('ALTER TABLE product_dynamic DROP COLUMN supplier')
    db.init_db(conn)
    assert dc.list_products(conn, 'hairdryer')[0]['supplier'] == '厂甲'
    conn.execute("UPDATE category_template SET supplier='新默认'")
    db.init_db(conn)
    assert dc.list_products(conn, 'hairdryer')[0]['supplier'] == '厂甲'


def test_ambiguous_existing_variants_raise_with_supplier_and_model():
    old = [{**draft(fp='old'), 'id':'a'}, {**draft(fp='older'), 'id':'b'}]
    with pytest.raises(ValueError, match='厂甲.*M1'):
        di._classify_rows(old, [draft()], ['model'])


def test_import_uses_row_supplier_then_document_vendor_then_category_default(conn, monkeypatch, tmp_path):
    seed(conn)
    monkeypatch.setattr(di.agent, 'parse_dynamic', lambda *a, **k: {'vendor':'文档厂', 'products':[{'model':'M1','supplier':'逐行厂'},{'model':'M2'}]})
    from openpyxl import Workbook
    path = tmp_path / 'supplier.xlsx'
    workbook = Workbook(); sheet = workbook.active
    sheet.append(['型号', '供应商']); sheet.append(['M1', '逐行厂']); sheet.append(['M2', ''])
    workbook.save(path)
    rows = di._agent_rows(dc.get_template(conn,'hairdryer'),path,tmp_path / 'work')
    assert [r['supplier'] for r in rows] == ['逐行厂','文档厂']


def test_approved_supplier_mutation_and_stale_supplier_edit(conn, client):
    seed(conn)
    dc.upsert_approved_products(conn, 'hairdryer', [{'id':'a','supplier':'原厂','data':{'model':'M1'}}])
    conn.commit()
    pending = client.patch('/products/hairdryer/a',headers=AUTH,json={'changes':{'supplier':'新厂'}})
    assert pending.status_code == 200
    ticket = pending.json()
    result = client.post(f'/tickets/{ticket["ticket_id"]}/decision',headers=AUTH,json={'token':ticket['token'],'approved':True})
    assert result.status_code == 200
    assert dc.list_products(conn,'hairdryer')[0]['supplier'] == '新厂'
    pending = client.patch('/products/hairdryer/a',headers=AUTH,json={'changes':{'model':'CHANGED'}}).json()
    client.patch('/products/hairdryer/a/direct',headers=AUTH,json={'changes':{'supplier':'第三厂'}})
    result = client.post(f'/tickets/{pending["ticket_id"]}/decision',headers=AUTH,json={'token':pending['token'],'approved':True})
    assert result.status_code == 409


def test_missing_variant_attributes_do_not_create_another_ambiguous_product():
    existing = [{**draft(color=color, fp=color), 'id':color} for color in ('红','蓝')]
    with pytest.raises(ValueError, match='无法确定'):
        di._classify_rows(existing, [draft(color='')], ['model'], ['color'])


def test_legacy_import_delist_drafts_do_not_remove_missing_products(conn, monkeypatch, tmp_path):
    seed(conn)
    dc.upsert_approved_products(conn,'hairdryer',[{**draft(fp='old'),'id':'a','cs_visible':1}])
    monkeypatch.setattr(di, '_agent_rows', lambda *a, **k: [draft(model='M2')])
    payload = di.build_product_payload(conn,'new.xlsx',tmp_path,source_key='new.xlsx',template_doc_id=None,category_key='hairdryer')
    payload['sheets'][0]['drafts']['delist'] = dc.list_products(conn,'hairdryer')
    result = di._apply_product_payload(conn,payload)
    assert result['delisted'] == 0
    assert next(p for p in dc.list_products(conn,'hairdryer') if p['id']=='a')['status'] == 'approved'


def test_supplierless_legacy_snapshot_is_rejected_without_changing_catalog(conn, monkeypatch, tmp_path):
    import hashlib, json
    from catalog.tickets import TicketConflict
    seed(conn)
    dc.upsert_approved_products(conn,'hairdryer',[{**draft(fp='old'),'id':'a','cs_visible':1}])
    before = dc.list_products(conn,'hairdryer')
    monkeypatch.setattr(di,'_agent_rows',lambda *a, **k: [draft()])
    payload = di.build_product_payload(conn,'new.xlsx',tmp_path,source_key='new.xlsx',template_doc_id=None,category_key='hairdryer')
    keys = ('id','inner_code','data','status','images','source_row','row_fingerprint','cs_visible')
    legacy = [{k:r.get(k) for k in keys} for r in before]
    payload['sheets'][0]['source_snapshot'] = hashlib.sha256(json.dumps(legacy,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with pytest.raises(TicketConflict,match='重新导入'):
        di._apply_product_payload(conn,payload)
    assert dc.list_products(conn,'hairdryer') == before


def test_repeated_incoming_variant_cannot_choose_an_arbitrary_update():
    old = [{**draft(fp='old'),'id':'a'}]
    with pytest.raises(ValueError, match='无法确定'):
        di._classify_rows(old,[draft(fp='one'),draft(fp='two')],['model'],['color'])


def test_multiline_model_supplier_selection_uses_first_line(conn):
    from catalog.csbot import CsBot
    seed(conn)
    dc.upsert_approved_products(conn, 'hairdryer', [
        {'id': product_id, 'supplier': supplier,
         'data': {'model': 'M1\n铝合金'}, 'cs_visible': 1}
        for product_id, supplier in [('a', '厂甲'), ('b', '厂乙')]])
    products = customer_catalog.local_catalog(conn)['products']
    assert {p['id'] for p in CsBot._matching_products('M1', products)} == {'a', 'b'}
    assert [p['id'] for p in CsBot._matching_products('厂乙 M1', products)] == ['b']
    assert [p['id'] for p in CsBot._matching_products('厂甲 M1', products)] == ['a']


def test_quote_details_and_notification_resolve_legacy_supplier_default(conn, client, monkeypatch):
    from catalog import notify
    from tests.conftest import DYNAMIC_FIELDS
    notices = []
    monkeypatch.setattr(notify, 'push_file', lambda message, path, **kw: notices.append(message))
    dc.approve_template(conn, {'key': 'legacy', 'name': '旧商品', 'supplier': '默认厂',
                              'fields': DYNAMIC_FIELDS})
    dc.upsert_approved_products(conn, 'legacy', [
        {'id': 'legacy-product', 'supplier': '', 'cs_visible': 1,
         'data': {'model': 'M1', 'price': '65', 'ctn': '40件/箱'}}])
    conn.commit()
    assert conn.execute("SELECT supplier FROM product_dynamic WHERE id='legacy-product'").fetchone()[0] == ''
    assert client.get('/products/legacy', headers=AUTH).json()['products'][0]['supplier'] == '默认厂'
    response = client.post('/quote', headers=AUTH, json={
        'items': [{'category': 'legacy', 'product_id': 'legacy-product', 'quantity': 50}],
        'price_adjustment_pct': 3})
    assert response.status_code == 200
    assert response.json()['items'][0]['supplier'] == '默认厂'
    assert '默认厂 M1' in response.json()['quantity_adjustment_note']
    assert '默认厂 M1' in notices[0]
