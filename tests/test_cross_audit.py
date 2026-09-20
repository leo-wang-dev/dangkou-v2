"""Adversarial combinations missing from the initial happy-path acceptance."""
import json
import pytest
from tests.test_purchase_conversation import setup, send, fields, seed_product


def test_export_does_not_bypass_merchant_redline(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个，帮我出表，能加急吗')
    assert b.api.documents
    assert 'bosswx' in '\n'.join(x[1] for x in b.api.sent)


def test_hidden_product_specs_never_disclosed_locally(setup,tmp_path,monkeypatch):
    c,b,m=setup;seed_product(c,tmp_path,monkeypatch)
    c.execute("UPDATE product_curler SET cs_visible=0,voltage='SECRET-VOLTAGE'");c.commit()
    send(b,'C001的电压是多少')
    assert 'SECRET-VOLTAGE' not in b.api.sent[-1][1]


def test_renaming_product_clears_old_identity_and_catalog_image(setup,tmp_path,monkeypatch):
    c,b,m=setup;seed_product(c,tmp_path,monkeypatch)
    m.actions=[{'op':'create','fields':{'型号或品名':'C001','数量':'100个'}}]
    send(b,'C001 100个')
    m.actions=[{'op':'update','index':1,'fields':{'型号或品名':'盘子'}}]
    send(b,'第一条换成盘子',2)
    assert fields(c)[0]['型号或品名']=='盘子'
    assert '商品编号' not in fields(c)[0]
    assert c.execute('SELECT photo FROM cs_note').fetchone()[0]==''


def test_extraction_outage_records_clear_item_and_does_not_block_owner_contact(setup,monkeypatch):
    c,b,m=setup
    original=m.chat_text
    def chat(system,*a,**kw):
        if '采购记录抽取' in system:raise TimeoutError('test timeout')
        return original(system,*a,**kw)
    monkeypatch.setattr(m,'chat_text',chat)
    send(b,'杯子100个，找老板')
    assert 'bosswx' in b.api.sent[-1][1]
    assert '已记录采购笔记' in b.api.sent[-1][1]
    assert fields(c) == [{'型号或品名':'杯子', '数量':'100个'}]


def test_selected_catalog_specs_exported_without_price(setup,tmp_path,monkeypatch):
    c,b,m=setup;inquiry=seed_product(c,tmp_path,monkeypatch)
    c.execute("UPDATE product_curler SET voltage='220V',price='5.00'");c.commit()
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}]);c.commit()
    send(b,'选1')
    assert fields(c)[0]['电压']=='220V'
    assert '5.00' not in json.dumps(fields(c))


def test_switching_selected_product_replaces_catalog_image_and_specs(setup,tmp_path,monkeypatch):
    from PIL import Image
    c,b,m=setup;inquiry=seed_product(c,tmp_path,monkeypatch)
    Image.new('RGB',(20,20),'red').save(tmp_path/'other.png')
    c.execute("UPDATE product_curler SET voltage='220V'")
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,status,cs_visible,image_main,voltage) VALUES('p2','C002','C002','approved',1,'other.png','110V')")
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}, {'category':'curler','product_id':'p2','name':'C002'}]);c.commit()
    send(b,'选1');send(b,'选2',2)
    assert fields(c)[0]['商品编号']=='p2'
    assert fields(c)[0]['电压']=='110V'
    assert c.execute('SELECT photo FROM cs_note').fetchone()[0].endswith('other.png')


def test_repeated_selection_keeps_provenance_for_later_rename(setup,tmp_path,monkeypatch):
    c,b,m=setup;inquiry=seed_product(c,tmp_path,monkeypatch)
    c.execute("UPDATE product_curler SET voltage='220V'")
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}]);c.commit()
    send(b,'选1');send(b,'选1',2)
    m.actions=[{'op':'update','index':1,'fields':{'型号或品名':'盘子'}}]
    send(b,'第一条换成盘子',3)
    assert '电压' not in fields(c)[0]


def test_renaming_preserves_customer_photo(setup,tmp_path,monkeypatch):
    c,b,m=setup;inquiry=seed_product(c,tmp_path,monkeypatch)
    send(b,photo=True)
    original=c.execute('SELECT photo FROM cs_note').fetchone()[0]
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}]);c.commit()
    send(b,'选1',2)
    m.actions=[{'op':'update','index':1,'fields':{'型号或品名':'盘子'}}]
    send(b,'第一条换成盘子',3)
    assert c.execute('SELECT photo FROM cs_note').fetchone()[0]==original
