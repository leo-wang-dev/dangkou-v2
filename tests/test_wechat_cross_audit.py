"""Independent regression checks for WeChat activation and local authority.

C 端 TG 绑定入口已拆（删C）：本组用例聚焦 activate 的本地授权语义——
微信托管激活不复活未审批的种籽红线、重启后仍稳定。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db, cs, tickets, wechat_binding


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('WECHAT_CUSTOMER_BOT_ENABLED','1')
    monkeypatch.setenv('WECHAT_MERCHANT_OWNER_IDS','owner')
    monkeypatch.setenv('WECHAT_BINDING_FILE',str(tmp_path/'binding.json'))
    conn=db.connect(str(tmp_path/'catalog.db'));db.init_db(conn)
    conn.execute("UPDATE shop_profile SET shop_name='微信原店',owner_wechat='shop-wechat',address='旧地址' WHERE id=1")
    conn.commit()
    app=FastAPI();app.state.conn=conn;app.state.token='service';wechat_binding.register(app)
    with TestClient(app, headers={'X-Service-Token':'service'}) as client:
        yield conn,client
    conn.close()


def test_wechat_management_starts_with_no_redline_before_binding(setup):
    conn, _ = setup
    assert cs.wechat_managed(conn)
    assert cs.get_redline(conn)['text_raw'] == ''


def test_product_approval_does_not_enable_unapproved_store_seed(setup):
    conn,client=setup
    t=tickets.create(conn,'redline',None,{'kind':'redline','product_id':'product-1','text_raw':'商品定制转人工'})
    tickets.decide(conn,t['id'],t['token'],True)
    assert cs.get_redline(conn)['text_raw']==''
    assert cs.get_redline(conn,'product-1')['text_raw']=='商品定制转人工'


def test_explicitly_approved_store_seed_survives(setup):
    conn,client=setup
    t=tickets.create(conn,'redline',None,{'kind':'redline','text_raw':cs.DEFAULT_STORE_REDLINE})
    tickets.decide(conn,t['id'],t['token'],True)
    wechat_binding.activate(conn);conn.commit()
    assert cs.get_redline(conn)['text_raw']==cs.DEFAULT_STORE_REDLINE


def test_restart_preserves_wechat_contacts_and_rules(setup):
    conn,client=setup
    conn.execute("UPDATE shop_profile SET owner_wechat='new-wechat',owner_tg_username='new_owner',address='新地址'")
    conn.commit()
    cs.set_redline(conn,None,'加急转人工')
    db.init_db(conn)
    wechat_binding.activate(conn);conn.commit()
    p=cs.get_shop(conn)
    assert p['owner_wechat']=='new-wechat' and p['owner_tg_username']=='new_owner' and p['address']=='新地址'
    assert cs.get_redline(conn)['text_raw']=='加急转人工'
    assert 'new-wechat' in cs.contact_reply(conn) and '@new_owner' in cs.contact_reply(conn)
