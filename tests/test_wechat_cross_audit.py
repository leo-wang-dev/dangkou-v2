"""Independent regression checks for WeChat activation and local authority."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db, cs, tickets, wechat_customer


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('WECHAT_CUSTOMER_BOT_ENABLED','1')
    monkeypatch.setenv('WECHAT_MERCHANT_OWNER_IDS','owner')
    monkeypatch.setenv('WECHAT_CUSTOMER_STATE',str(tmp_path/'runtime'))
    conn=db.connect(str(tmp_path/'catalog.db'));db.init_db(conn)
    conn.execute("UPDATE shop_profile SET shop_name='微信原店',owner_wechat='shop-wechat',address='旧地址' WHERE id=1")
    conn.commit()
    monkeypatch.setattr(wechat_customer,'identity',lambda token:{'id':222222,'username':'test_customer_bot','is_bot':True})
    app=FastAPI();app.state.conn=conn;app.state.token='service';wechat_customer.register(app)
    with TestClient(app, headers={'X-Service-Token':'service'}) as client:
        yield conn,client
    conn.close()


def bind(client, suffix='a'):
    return client.post('/wechat/customer-bot/bind',json={'owner_id':'owner','token':'222222:'+suffix*35})


def test_wechat_management_starts_with_no_redline_before_customer_bot_binding(setup):
    conn, _ = setup
    assert cs.wechat_managed(conn)
    assert cs.get_redline(conn)['text_raw'] == ''


def test_product_approval_does_not_enable_unapproved_store_seed(setup):
    conn,client=setup
    t=tickets.create(conn,'redline',None,{'kind':'redline','product_id':'product-1','text_raw':'商品定制转人工'})
    tickets.decide(conn,t['id'],t['token'],True)
    assert bind(client).status_code==200
    assert cs.get_redline(conn)['text_raw']==''
    assert cs.get_redline(conn,'product-1')['text_raw']=='商品定制转人工'


def test_explicitly_approved_store_seed_survives(setup):
    conn,client=setup
    t=tickets.create(conn,'redline',None,{'kind':'redline','text_raw':cs.DEFAULT_STORE_REDLINE})
    tickets.decide(conn,t['id'],t['token'],True)
    assert bind(client).status_code==200
    assert cs.get_redline(conn)['text_raw']==cs.DEFAULT_STORE_REDLINE


def test_token_rotation_preserves_wechat_contacts_and_rules(setup):
    conn,client=setup
    assert bind(client).status_code==200
    conn.execute("UPDATE shop_profile SET owner_wechat='new-wechat',owner_tg_username='new_owner',address='新地址'")
    conn.commit()
    cs.set_redline(conn,None,'加急转人工')
    assert bind(client,'b').status_code==200
    db.init_db(conn)
    p=cs.get_shop(conn)
    assert p['owner_wechat']=='new-wechat' and p['owner_tg_username']=='new_owner' and p['address']=='新地址'
    assert cs.get_redline(conn)['text_raw']=='加急转人工'
    assert 'new-wechat' in cs.contact_reply(conn) and '@new_owner' in cs.contact_reply(conn)


def test_missing_contacts_cannot_open_customer_service(setup):
    conn,client=setup
    conn.execute("UPDATE shop_profile SET owner_wechat='',owner_tg_username=''")
    conn.commit()
    response=bind(client)
    assert response.status_code==409
    assert not wechat_customer.secret_path().exists()
