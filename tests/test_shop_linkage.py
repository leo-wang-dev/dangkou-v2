import io
import json
import sqlite3

import openpyxl
import pytest
from catalog import db, shop_link, tickets
from tests.test_release_gates import env, auth, photo


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
    with pytest.raises(ValueError):shop_link.verify_bot(conn,{'id':12345,'is_bot':True})
    assert client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True}).status_code==200
    assert shop_link.verify_bot(conn,{'id':12345,'is_bot':True})==shop_id
    assert conn.execute("SELECT shop_id FROM product_curler WHERE id='p1'").fetchone()[0]==shop_id
    bot.handle_update(photo(8101))
    note=conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchone()
    assert note['received_shop_id']==note['source_shop_id']==shop_id
    assert note['source_basis']=='bot_context'
    assert '测试义乌美妆档口' in bot.api.send_message.call_args.args[1]
    cust=conn.execute('SELECT * FROM cs_customer WHERE id=?',(note['customer_id'],)).fetchone()
    bot._make_link(cust)
    token=conn.execute('SELECT token FROM cs_link WHERE customer_id=?',(cust['id'],)).fetchone()[0]
    listed=client.get('/cs/link/'+token).json()['notes'][0]
    assert 'source_shop_id' not in listed and 'received_shop_id' not in listed
    assert listed['fields']['档口名称']=='测试义乌美妆档口'
    assert 'TEST-WX' in listed['fields']['供应商联系方式']
    sheet=openpyxl.load_workbook(io.BytesIO(client.get('/cs/link/'+token+'/export.xlsx').content)).active
    assert sheet.max_row==2 and len(sheet._images)==1
    assert '接待档口（供货关系待确认）' in str(list(sheet.values))
    document=json.loads(conn.execute("SELECT body FROM cs_outbox WHERE channel='tg_document'").fetchone()[0])
    assert json.loads(document['notes'][0]['fields_json'])['档口名称']=='测试义乌美妆档口'
    approve(client,{'shop_name':'测试档口新名','owner_wechat':'TEST-NEW'})
    fields=client.get('/cs/link/'+token).json()['notes'][0]['fields']
    assert fields['档口名称']=='测试档口新名' and 'TEST-NEW' in fields['供应商联系方式']
    # Already queued files retain their original snapshot rather than changing on retry.
    assert json.loads(document['notes'][0]['fields_json'])['档口名称']=='测试义乌美妆档口'
    linkage=client.get('/shop/linkage',headers=auth()).json()
    assert linkage['shop_id']==shop_id and linkage['catalog_counts']['curler']==1
    assert client.get('/shop/linkage').status_code==401


def test_external_source_override_does_not_mix_current_owner_contacts(env):
    conn,client,bot=env
    approve(client,{'shop_name':'本店测试名','tg_bot_id':'12345','owner_wechat':'PRIVATE-OWNER'})
    bot.handle_update(photo(8102))
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
    with pytest.raises(ValueError,match='不一致'):shop_link.verify_bot(conn,{'id':99999,'is_bot':True})
    assert client.patch('/shop',headers=auth(),json={'changes':{'tg_bot_id':'99999'}}).status_code==400
    assert client.patch('/shop',headers=auth(),json={'changes':{'shop_id':'other'}}).status_code==400
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO product_curler(id,inner_code,shop_id) VALUES('bad','BAD','another-shop')")
    conn.rollback()


def test_competing_shop_approvals_do_not_replace_newer_values(env):
    _,client,_=env
    first=client.patch('/shop',headers=auth(),json={'changes':{'shop_name':'甲'}}).json()
    second=client.patch('/shop',headers=auth(),json={'changes':{'shop_name':'乙'}}).json()
    assert client.post(f"/tickets/{first['ticket_id']}/decision",json={'token':first['token'],'approved':True}).status_code==200
    assert client.post(f"/tickets/{second['ticket_id']}/decision",json={'token':second['token'],'approved':True}).status_code==409


def test_legacy_database_migration_preserves_products_and_unknown_note_origins(tmp_path):
    path=tmp_path/'old.db'
    conn=db.connect(str(path))
    conn.execute('CREATE TABLE product_curler(id TEXT PRIMARY KEY,inner_code TEXT UNIQUE NOT NULL,item_no TEXT,price TEXT,status TEXT,image_main TEXT,images TEXT,source_doc INTEGER,created_at TEXT,updated_at TEXT)')
    conn.execute("INSERT INTO product_curler VALUES('legacy','L1','OLD-MODEL','9.99','approved',NULL,'[]',NULL,'2020','2021')")
    conn.commit();db.init_db(conn)
    sid=shop_link.profile(conn)['shop_id']
    row=conn.execute("SELECT * FROM product_curler WHERE id='legacy'").fetchone()
    assert row['price']=='9.99' and row['shop_id']==sid and row['cs_visible']==0
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('legacy-c','old')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json) VALUES('legacy-c','{}')");conn.commit()
    db.init_db(conn);assert shop_link.profile(conn)['shop_id']==sid
    old=conn.execute('SELECT * FROM cs_note').fetchone()
    assert old['received_shop_id'] is None and old['source_shop_id'] is None
    assert shop_link.fields_for(conn,old)['档口名称']=='待补充'
    conn.execute("INSERT INTO product_curler(id,inner_code) VALUES('new','N1')")
    assert conn.execute("SELECT shop_id FROM product_curler WHERE id='new'").fetchone()[0]==sid
    conn.close()


def test_mismatched_bot_stops_before_polling_or_flushing(env,monkeypatch):
    from unittest.mock import Mock
    from scripts import run_cs_bot
    conn,client,_=env
    approve(client,{'shop_name':'档口甲','tg_bot_id':'12345'})
    api=Mock();api._call.return_value={'id':99999,'is_bot':True}
    factory=Mock()
    monkeypatch.setattr(run_cs_bot.db,'connect',lambda:conn)
    monkeypatch.setattr(run_cs_bot,'TgApi',lambda:api)
    monkeypatch.setattr(run_cs_bot,'CsBot',factory)
    with pytest.raises(ValueError,match='不一致'):run_cs_bot.main()
    api.poll.assert_not_called();factory.assert_not_called()


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
