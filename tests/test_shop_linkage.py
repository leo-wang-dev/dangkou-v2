import io
import json
import sqlite3

import openpyxl
import pytest
from catalog import db, shop_link, tickets
from tests.test_release_gates import env, auth, take_photo


def approve(client, changes):
    response=client.patch('/shop',headers=auth(),json={'changes':changes})
    assert response.status_code==200,response.text
    tk=response.json()
    result=client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True})
    assert result.status_code==200,result.text
    return tk


def test_merchant_approval_bot_photo_web_excel_same_identity(env):
    conn,client,bot=env
    shop_id=client.get('/shop',headers=auth()).json()['shop_id']
    changes={'shop_name':'测试义乌美妆档口','stall_no':'A区123','contact_name':'测试老板','tg_bot_id':'12345',
             'tg_bot_username':'test_shop_bot','owner_tg_username':'test_owner','owner_wechat':'TEST-WX','address':'测试路'}
    tk=client.patch('/shop',headers=auth(),json={'changes':changes}).json()
    assert not shop_link.profile(conn)['shop_name']
    assert client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True}).status_code==200
    assert shop_link.profile(conn)['tg_bot_id']=='12345'
    assert conn.execute("SELECT shop_id FROM product_dynamic WHERE id='p1'").fetchone()[0]==shop_id
    receipt=take_photo(bot)
    note=conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchone()
    assert note['received_shop_id']==note['source_shop_id']==shop_id
    assert note['source_basis']=='bot_context'
    assert '测试义乌美妆档口' in receipt
    cust=conn.execute('SELECT * FROM cs_customer WHERE id=?',(note['customer_id'],)).fetchone()
    bot._make_link(cust)
    token=conn.execute('SELECT token FROM cs_link WHERE customer_id=?',(cust['id'],)).fetchone()[0]
    listed=client.get('/cs/link/'+token).json()['notes'][0]
    assert 'source_shop_id' not in listed and 'received_shop_id' not in listed
    assert listed['fields']['档口名称']=='测试义乌美妆档口'
    assert 'TEST-WX' in listed['fields']['供应商联系方式']
    sheet=openpyxl.load_workbook(io.BytesIO(client.get('/cs/link/'+token+'/export.xlsx').content)).active
    assert sheet.max_row==6 and len(sheet._images)==1
    rows_export = list(sheet.values)
    assert {'档口名称', '供应商联系方式'} <= set(rows_export[4])  # 供货身份列必须在
    assert '待确认' in str(rows_export[5])  # 未确认的供货关系明示给商家
    approve(client,{'shop_name':'测试档口新名','owner_wechat':'TEST-NEW'})
    fields=client.get('/cs/link/'+token).json()['notes'][0]['fields']
    assert fields['档口名称']=='测试义乌美妆档口' and 'TEST-WX' in fields['供应商联系方式']  # batch preset is immutable
    linkage=client.get('/shop/linkage',headers=auth()).json()
    assert linkage['shop_id']==shop_id and linkage['catalog_counts']['audit_cat']==1
    assert client.get('/shop/linkage').status_code==401


def test_external_source_override_does_not_mix_current_owner_contacts(env):
    conn,client,bot=env
    approve(client,{'shop_name':'本店测试名','tg_bot_id':'12345','owner_wechat':'PRIVATE-OWNER'})
    take_photo(bot)
    note=conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchone()
    cust=conn.execute('SELECT * FROM cs_customer WHERE id=?',(note['customer_id'],)).fetchone()
    bot._on_text(cust,'清单第1条 档口：外部档口')
    note=conn.execute('SELECT * FROM cs_note WHERE id=?',(note['id'],)).fetchone()
    assert note['received_shop_id'] and note['source_shop_id'] is None
    assert shop_link.fields_for(conn,note)['供应商联系方式']=='待补充'
    assert note['source_basis']=='manual'
    bot._on_text(cust,'清单第1条 档口：本店')
    note=conn.execute('SELECT * FROM cs_note WHERE id=?',(note['id'],)).fetchone()
    assert note['source_shop_id']==note['received_shop_id'] and note['source_basis']=='customer_confirmed'
    external={k:'待补充' for k in shop_link.SUPPLIER_FIELDS};external['档口名称']='照片供应商'
    received,source,basis=shop_link.origin(conn,external)
    assert received and source is None and basis=='photo'


def test_bot_binding_mismatch_and_rebind_rejected(env):
    conn,client,bot=env
    assert client.patch('/shop',headers=auth(),json={'changes':{'tg_bot_id':'12345'}}).status_code==400
    approve(client,{'shop_name':'档口甲','tg_bot_id':'12345'})
    assert client.patch('/shop',headers=auth(),json={'changes':{'tg_bot_id':'99999'}}).status_code==400
    assert client.patch('/shop',headers=auth(),json={'changes':{'shop_id':'other'}}).status_code==400
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO product_dynamic(id,category_key,inner_code,shop_id) "
                     "VALUES('bad','audit_cat','BAD','another-shop')")
    conn.rollback()


def test_competing_shop_approvals_do_not_replace_newer_values(env):
    _,client,_=env
    first=client.patch('/shop',headers=auth(),json={'changes':{'shop_name':'甲'}}).json()
    second=client.patch('/shop',headers=auth(),json={'changes':{'shop_name':'乙'}}).json()
    assert client.post(f"/tickets/{first['ticket_id']}/decision",json={'token':first['token'],'approved':True}).status_code==200
    assert client.post(f"/tickets/{second['ticket_id']}/decision",json={'token':second['token'],'approved':True}).status_code==409


def test_legacy_database_migration_preserves_products_and_unknown_note_origins(tmp_path):
    """老库迁移（product_dynamic 无 shop_id 列）：补列+回填归属；未知来源笔记不猜档口。"""
    import json as _json
    from pathlib import Path
    path=tmp_path/'old.db'
    conn=db.connect(str(path))
    old_schema=Path(db._SCHEMA).read_text().split('-- ===== C端')[0]
    conn.executescript(old_schema)                      # 旧结构：product_dynamic 无 shop_id 列
    conn.execute('DELETE FROM product_dynamic')
    conn.commit(); db.init_db(conn)
    from catalog import dynamic_catalog
    dynamic_catalog.approve_template(conn, {'key': 'old_cat', 'name': '旧库品类',
        'source_sheet': '旧库品类', 'fields': [
            {'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'}]})
    conn.execute("INSERT INTO product_dynamic(id,category_key,inner_code,data_json) "
                 "VALUES('legacy','old_cat','L1',?)", (_json.dumps({'model': 'OLD-MODEL'}),))
    conn.commit(); db.init_db(conn)
    sid=shop_link.profile(conn)['shop_id']
    row=conn.execute("SELECT * FROM product_dynamic WHERE id='legacy'").fetchone()
    assert _json.loads(row['data_json'])['model']=='OLD-MODEL' and row['shop_id']==sid
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('legacy-c','old')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json) VALUES('legacy-c','{}')");conn.commit()
    db.init_db(conn);assert shop_link.profile(conn)['shop_id']==sid
    old=conn.execute('SELECT * FROM cs_note').fetchone()
    assert old['received_shop_id'] is None and old['source_shop_id'] is None
    assert shop_link.fields_for(conn,old)['档口名称']=='待补充'
    conn.execute("INSERT INTO product_dynamic(id,category_key,inner_code) VALUES('new','old_cat','N1')")
    assert conn.execute("SELECT shop_id FROM product_dynamic WHERE id='new'").fetchone()[0]==sid
    conn.close()


def test_linkage_check_rejects_other_database_and_unconfigured():
    from scripts.check_shop_linkage import compare
    local={'shop_id':'a','shop_name':'档口甲','tg_bot_id':'12345'}
    compare(local,{**local,'configured':True})
    with pytest.raises(ValueError):compare(local,{**local,'shop_id':'b','configured':True})
    with pytest.raises(ValueError):compare(local,{**local,'tg_bot_id':'99999','configured':True})
    with pytest.raises(ValueError):compare({**local,'shop_name':''},{**local,'configured':True})


def test_linkage_reports_dynamic_categories_and_customer_visible_boundary(env, tmp_path):
    conn, client, _ = env
    approve(client, {'shop_name': '动态分类档口', 'tg_bot_id': '12345'})
    from catalog.dynamic_import import build_ticket_payload
    from tests.test_workbook_templates import blowdryer_fixture
    payload = build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='linkage')
    ticket = tickets.create(conn, 'template_import', None, payload)
    tickets.decide(conn, ticket['id'], ticket['token'], True)
    category = payload['sheets'][0]['template']['key']
    category_name = payload['sheets'][0]['template']['name']
    linkage = client.get('/shop/linkage', headers=auth()).json()
    item = next(value for value in linkage['dynamic_categories'] if value['key'] == category)
    assert item['name'] == category_name and item['count'] == 4 and item['customer_visible'] == 4
    assert linkage['catalog_counts'][category] == 4
    assert linkage['customer_visible_counts'][category] == 4
