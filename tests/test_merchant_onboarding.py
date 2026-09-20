import json
import os
import sqlite3
import time
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db, merchant_onboarding as hub, merchant_binding as binding, merchant_policy
from scripts.run_merchant_hub import ingest,process,flush

@pytest.fixture
def c(tmp_path,monkeypatch):
    monkeypatch.setenv('MERCHANT_HUB_DB',str(tmp_path/'hub.db'))
    monkeypatch.setenv('MERCHANT_RUNTIME_DIR',str(tmp_path/'shops'))
    monkeypatch.setenv('ONBOARDING_PUBLIC_URL','https://example.test')
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
    conn=hub.connect();yield conn;conn.close()

def finish(c,owner='123',price='跳过'):
    hub.handle(c,owner,'创建新档口')
    for value in ['测试档口','@shop_owner','wxshop','满箱请与店主确认',price,*(['跳过']*5)]:hub.handle(c,owner,value)
    result=hub.handle(c,owner,'确认配置');c.commit();return result

def test_draft_skip_confirm_resume(c):
    hub.handle(c,'123','创建新档口');hub.handle(c,'123','店铺')
    hub.handle(c,'123','稍后继续');hub.handle(c,'123','随便一句')
    assert hub.account(c,'123')['step']==1
    hub.handle(c,'123','继续配置')
    for _ in range(9):hub.handle(c,'123','跳过')
    assert hub.account(c,'123')['confirmed']=='{}'
    assert '配置已确认' in hub.handle(c,'123','确认配置')
    assert not any(json.loads(hub.account(c,'123')['confirmed']).get(k) for k in hub.RULE_KEYS)
    assert hub.account(c,'999') is None

def test_contact_required_only_for_rules(c):
    hub.handle(c,'123','创建新档口')
    for value in ['店铺','跳过','跳过','跳过','议价转人工',*(['跳过']*5)]:hub.handle(c,'123',value)
    assert '联系方式' in hub.handle(c,'123','确认配置')
    assert hub.account(c,'123')['confirmed']=='{}'

def test_invite_one_use_and_no_name_claim(c,tmp_path):
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source);source.close()
    token=verified_invite(c,str(tmp_path/'shop.db'),owner='1')
    assert '档口名称' in hub.handle(c,'1','绑定已有档口 '+token)
    assert '已使用' in hub.handle(c,'2','绑定已有档口 '+token)
    assert hub.account(c,'2') is None
    assert '无效' in hub.handle(c,'3','绑定已有档口 店铺名称')

def test_private_identity_dedup_and_secret_redaction(c):
    token='123456:'+('a'*35)
    updates=[{'update_id':1,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':token}},
             {'update_id':2,'message':{'from':{'id':123},'chat':{'id':555,'type':'private'},'text':'创建新档口'}}]
    ingest(c,updates);ingest(c,updates);process(c);process(c)
    assert c.execute('SELECT count(*) FROM hub_outbox').fetchone()[0]==1
    assert token not in str([tuple(x) for x in c.execute('SELECT * FROM hub_inbox')])
    assert hub.account(c,'123') is None
    sent=[];api=SimpleNamespace(send_message=lambda *args:sent.append(args));flush(c,api);flush(c,api)
    assert len(sent)==1

@pytest.fixture
def client(c,monkeypatch):
    app=FastAPI();app.state.token='admin';binding.register(app)
    monkeypatch.setattr(binding,'identity',lambda token:{'id':222222,'username':'merchant_bot','is_bot':True})
    return TestClient(app,base_url='https://example.test')

def form_key(c,owner='123'):
    hub.link(c,hub.account(c,owner));c.commit()
    # Issue known test capability; production stores only its digest.
    key='k'*40
    c.execute('UPDATE merchant SET binding_hash=?,binding_expires=? WHERE owner=?',(hub.digest(key),int(time.time())+100,owner));c.commit()
    return key

def test_binding_confirm_replay_and_permissions(c,client):
    finish(c);key=form_key(c);token='222222:'+('x'*35)
    body={'key':key,'token':token}
    r=client.post('/merchant/binding',json=body);assert r.status_code==200
    assert hub.account(c,'123')['bot_id'] is None
    r=client.post('/merchant/binding',json={**body,'confirm_bot_id':'222222'});assert r.status_code==200
    m=hub.account(c,'123');path=binding.credentials(m['id'])
    assert path.stat().st_mode & 0o777 == 0o600
    assert token not in str(tuple(m))
    assert client.post('/merchant/binding',json=body).status_code==410

def test_binding_https_origin_and_validation(c,client):
    finish(c);key=form_key(c)
    body={'key':key,'token':'222222:'+('x'*35)}
    assert client.post('/merchant/binding',json=body,headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('http://example.test/merchant/binding',json=body).status_code==400
    r=client.post('/merchant/binding',json={**body,'key':'bad'})
    assert r.status_code==422 and body['token'] not in r.text
    assert client.get('/merchant/customer/'+'a'*24+'/admin').status_code==404


def test_management_shell_always_contains_wechat_binding_tab(c, client):
    """The hub serves the current shell even when a worker has an older build."""
    finish(c)
    m = hub.account(c, '123')
    c.execute("UPDATE merchant SET state='catalog_ready',runtime_status='running' WHERE id=?", (m['id'],))
    c.commit()
    link = hub.handle(c, '123', '商品管理')
    c.commit()
    key = link.split('?t=', 1)[1].split('\n', 1)[0]
    response = client.get(f"/merchant/manage/{m['id']}/?t={key}")
    assert response.status_code == 200
    assert 'id="v-wechat"' in response.text
    assert 'id="wechat-frame"' in response.text

def test_duplicate_bot_and_expired_link(c,client):
    finish(c,'123');key=form_key(c)
    body={'key':key,'token':'222222:'+('x'*35),'confirm_bot_id':'222222'}
    assert client.post('/merchant/binding',json=body).status_code==200
    finish(c,'456');key=form_key(c,'456');body['key']=key
    assert client.post('/merchant/binding',json=body).status_code==409
    c.execute('UPDATE merchant SET binding_expires=0');c.commit()
    assert client.post('/merchant/binding',json=body).status_code==410

def test_platform_and_webhook_rejected(monkeypatch):
    monkeypatch.setenv('TG_BOT_TOKEN','123456:'+('p'*35))
    with pytest.raises(Exception) as exc:binding.identity('123456:'+('p'*35))
    assert exc.value.status_code==409
    class Api:
        def __init__(self,**kwargs):pass
        def _call(self,method):return {'id':555555,'username':'shop_bot','is_bot':True} if method=='getMe' else {'url':'https://other.test'}
    monkeypatch.setattr(binding,'TgApi',Api)
    with pytest.raises(Exception) as exc:binding.identity('555555:'+('p'*35))
    assert exc.value.status_code==409

def test_provision_isolated_and_rules_explicit(c):
    from scripts.run_merchant_runtime import provision
    paths=[]
    for owner in ('123','456'):
        finish(c,owner);m=hub.account(c,owner)
        c.execute("UPDATE merchant SET state='enabled',bot_id=?,bot_username=? WHERE id=?",(owner,'shop_bot',m['id']));c.commit()
        binding.write_secret(binding.credentials(m['id']),{'bot_token':owner+':test','api_token':'secret-'+owner})
        env,port,with_bot=provision(c,hub.account(c,owner));paths.append(env['CATALOG_V2_DB'])
        assert with_bot
        assert env['MERCHANT_HUB_ENABLED']=='0'
        s=db.connect(paths[-1]);policy=merchant_policy.read(s)
        assert policy['shop_name']=='测试档口' and not policy.get('price')
        assert s.execute('SELECT tg_bot_id FROM shop_profile').fetchone()[0]==owner;s.close()
    assert paths[0]!=paths[1]

def test_price_and_quote_rules_never_bypass_platform_handoff(tmp_path):
    from catalog.csbot import CsBot
    conn=db.connect(str(tmp_path/'shop.db'));db.init_db(conn)
    conn.execute("UPDATE shop_profile SET owner_wechat='ownerwx' WHERE id=1")
    calls=[]
    llm=SimpleNamespace(chat_text=lambda *a,**kw:calls.append(a) or 'TRANSFER')
    bot=CsBot(conn,None,llm=llm,img_dir=str(tmp_path/'photos'))
    cust=bot._ensure_customer({'id':123,'first_name':'test'})
    merchant_policy.apply(conn,{'shop_name':'测试','quote_rules':'整箱议价'},1)
    reply = bot._on_text(cust,'拿1000件多少钱')
    assert 'ownerwx' not in reply and '整箱议价' not in reply
    assert calls==[]
    merchant_policy.apply(conn,{'shop_name':'测试','price':'议价转人工'},2)
    assert 'ownerwx' in bot._on_text(cust,'能便宜吗')
    assert len(calls)==1
    merchant_policy.apply(conn,{'shop_name':'测试'},3)
    assert 'ownerwx' not in bot._on_text(cust,'多少钱')
    assert calls==[calls[0]]

def test_migration_preserves_notes_and_archives_offsets(c,tmp_path):
    from scripts.migrate_merchant_bot import migrate
    source=db.connect(str(tmp_path/'old.db'));db.init_db(source)
    source.execute("UPDATE shop_profile SET tg_bot_id='111111',shop_name='原档口' WHERE id=1")
    source.execute("INSERT INTO cs_customer(id,tg_id) VALUES('c1','123')")
    source.execute("INSERT INTO cs_note(customer_id,photo,fields_json,status) VALUES('c1','','{}','confirmed')")
    source.execute("INSERT INTO cs_inbox(update_id,payload,processed) VALUES(999,'{}',1)");source.commit()
    invite=verified_invite(c,str(tmp_path/'old.db'));hub.handle(c,'123','绑定已有档口 '+invite)
    for _ in hub.STEPS:hub.handle(c,'123','跳过')
    hub.handle(c,'123','确认配置');m=hub.account(c,'123')
    c.execute("UPDATE merchant SET bot_id='222222',bot_username='new_bot' WHERE id=?",(m['id'],));c.commit()
    binding.write_secret(binding.credentials(m['id']),{'bot_token':'222222:test'})
    result=migrate(c,m['id'])
    assert result['old_bot_last_update']==999
    assert source.execute('SELECT count(*) FROM cs_note').fetchone()[0]==1
    assert source.execute('SELECT count(*) FROM cs_inbox').fetchone()[0]==0
    assert source.execute('SELECT tg_bot_id FROM shop_profile').fetchone()[0]=='222222'
    assert os.stat(result['backup']).st_mode & 0o777 == 0o600
    source.close()

def test_editing_confirmed_rules_requires_second_confirmation(c):
    finish(c,price='最低价转人工')
    before=hub.account(c,'123')['confirmed']
    hub.handle(c,'123','修改配置')
    for _ in range(4):hub.handle(c,'123','保持不变')
    hub.handle(c,'123','清空')
    assert hub.account(c,'123')['confirmed']==before
    for _ in range(5):hub.handle(c,'123','跳过')
    hub.handle(c,'123','确认配置')
    assert json.loads(hub.account(c,'123')['confirmed'])['price']==''

def test_category_query_never_requires_policy_inference(tmp_path):
    from catalog.csbot import CsBot
    conn=db.connect(str(tmp_path/'shop.db'));db.init_db(conn)
    def forbidden(*a,**kw):raise AssertionError('simple catalog query must not invoke model')
    bot=CsBot(conn,None,llm=SimpleNamespace(chat_text=forbidden),img_dir=str(tmp_path/'photos'))
    cust=bot._ensure_customer({'id':123})
    assert '当前没有在线商品' in bot._on_text(cust,'你好，请问你们卖什么品类？')

def test_claim_deep_link(c,tmp_path):
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source);source.close()
    token=verified_invite(c,str(tmp_path/'shop.db'))
    assert '档口名称' in hub.handle(c,'123','/start claim_'+token)
    assert hub.account(c,'123')['db_path']==str(tmp_path/'shop.db')

def test_activation_migrates_only_retired_platform_binding(c,tmp_path,monkeypatch):
    from scripts.run_merchant_runtime import provision
    monkeypatch.setenv('TG_BOT_TOKEN','111111:platform')
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source)
    source.execute("UPDATE shop_profile SET tg_bot_id='111111',shop_name='老店' WHERE id=1");source.commit()
    token=verified_invite(c,str(tmp_path/'shop.db'));hub.handle(c,'123','/start claim_'+token)
    for _ in hub.STEPS:hub.handle(c,'123','跳过')
    hub.handle(c,'123','确认配置');m=hub.account(c,'123')
    c.execute("UPDATE merchant SET state='enabled',bot_id='222222',bot_username='new_bot',runtime_status='starting' WHERE id=?",(m['id'],));c.commit()
    binding.write_secret(binding.credentials(m['id']),{'bot_token':'222222:test','api_token':'test'})
    provision(c,hub.account(c,'123'))
    assert source.execute('SELECT tg_bot_id FROM shop_profile').fetchone()[0]=='222222'
    assert list(tmp_path.glob('shop.db.before-bot-*'))
    source.close()


def verified_invite(c,path,owner='123'):
    from catalog.merchant_verification import review
    token=hub.new_invite(c,path)
    assert '申请已提交' in hub.handle(c,owner,'绑定已有档口 '+token)
    c.commit()
    rid=c.execute('SELECT id FROM ownership_request WHERE owner=?',(owner,)).fetchone()[0]
    rev=prepare_contact(c,rid,owner)
    review(c,rid,True,'fixture-admin','fixture-existing-contact-confirmation',True,rev)
    return token

def test_forwarded_invite_requires_independent_review(c,tmp_path):
    from catalog.merchant_verification import review
    source=db.connect(str(tmp_path/'private.db'));db.init_db(source)
    source.execute("UPDATE shop_profile SET shop_name='PRIVATE SHOP',owner_wechat='private_owner' WHERE id=1");source.commit();source.close()
    token=hub.new_invite(c,str(tmp_path/'private.db'))
    for owner in ('owner','attacker'):
        reply=hub.handle(c,owner,'绑定已有档口 '+token)
        assert '申请已提交' in reply and 'PRIVATE SHOP' not in reply and 'private_owner' not in reply
        assert hub.account(c,owner) is None
    c.commit()
    rid=c.execute("SELECT id FROM ownership_request WHERE owner='owner'").fetchone()[0]
    rev=prepare_contact(c,rid,'owner')
    review(c,rid,True,'operator','original-wechat-confirmation-20260918',True,rev)
    assert '申请已提交' in hub.handle(c,'attacker','绑定已有档口 '+token)
    assert hub.account(c,'attacker') is None
    c.commit()
    assert '档口名称' in hub.handle(c,'owner','认证进度')
    assert hub.account(c,'owner')['db_path']==str(tmp_path/'private.db')
    assert '已通过' in hub.handle(c,'owner','认证进度')
    assert json.loads(hub.account(c,'owner')['draft'])['shop_name']=='PRIVATE SHOP'
    c.commit()
    bad=c.execute("SELECT id FROM ownership_request WHERE owner='attacker'").fetchone()[0]
    with pytest.raises(ValueError):review(c,bad,True,'operator','attacker-knows-phone-number')

def test_review_requires_admin_and_evidence(c,client,tmp_path):
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source);source.close()
    token=hub.new_invite(c,str(tmp_path/'shop.db'));hub.handle(c,'123','绑定已有档口 '+token);c.commit()
    rid=c.execute('SELECT id FROM ownership_request').fetchone()[0]
    rev=prepare_contact(c,rid,'123')
    payload={'approved':True,'reviewer':'admin','evidence_reference':'original-contact-proof-001','holder_confirmed':True,'contact_revision':rev}
    path='/merchant/ownership-requests/'+rid+'/review'
    assert client.post(path,json=payload).status_code==401
    assert client.get('/merchant/ownership-requests').status_code==401
    assert client.post(path,json={**payload,'evidence_reference':''},headers={'X-Service-Token':'admin'}).status_code==422
    assert client.post(path,json=payload,headers={'X-Service-Token':'admin'}).status_code==200
    assert c.execute('SELECT count(*) FROM ownership_audit').fetchone()[0]==1

def test_preexisting_bearer_claim_is_not_grandfathered(c,tmp_path):
    from catalog.merchant_verification import verified
    from scripts.run_merchant_runtime import provision
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source);source.close()
    token=hub.new_invite(c,str(tmp_path/'shop.db'))
    c.execute("INSERT INTO merchant(id,owner,db_path,invitation_used,draft) VALUES('legacy','123',?,?,?)",(str(tmp_path/'shop.db'),hub.digest(token),json.dumps({'shop_name':'secret'})))
    c.execute('UPDATE invitation SET used_by=?',('123',));c.commit()
    m=hub.account(c,'123');assert not verified(c,m)
    assert 'secret' not in hub.handle(c,'123','查看配置')
    assert '申请已提交' in hub.handle(c,'123','启用客服')
    with pytest.raises(ValueError,match='尚未认证'):provision(c,m)
    c.execute('UPDATE merchant SET binding_hash=?,binding_expires=9999999999',(hub.digest('k'*40),));c.commit()
    with pytest.raises(Exception) as e:binding.lookup(c,'k'*40)
    assert e.value.status_code==403


def prepare_contact(c,rid,owner,value='fixture_original_owner'):
    from catalog.ownership_registry import save,match
    rev=save(c,rid,'wechat',value,'fixture-historical-record-001','fixture-admin')
    assert '登记资料匹配' in match(c,owner,value)
    c.commit()
    return rev


def test_phone_match_is_not_ownership_proof(c,tmp_path):
    from catalog.ownership_registry import save,match
    from catalog.merchant_verification import review
    source=db.connect(str(tmp_path/'phone.db'));db.init_db(source);source.close()
    token=hub.new_invite(c,str(tmp_path/'phone.db'));hub.handle(c,'123','绑定已有档口 '+token);c.commit()
    rid=c.execute('SELECT id FROM ownership_request').fetchone()[0]
    with pytest.raises(ValueError,match='原有登记'):review(c,rid,True,'admin','test-evidence-01',True,1)
    rev=save(c,rid,'phone','13800138000','historical-fixture-record','admin')
    assert '登记资料匹配' in hub.handle(c,'123','认证手机号 13800138000');c.commit()
    with pytest.raises(ValueError,match='持有人'):review(c,rid,True,'admin','test-evidence-01',False,rev)
    new=save(c,rid,'phone','13900139000','historical-correction-record','admin')
    with pytest.raises(ValueError,match='版本变化'):review(c,rid,True,'admin','test-evidence-01',True,rev)
    with pytest.raises(ValueError,match='尚未完成'):review(c,rid,True,'admin','test-evidence-01',True,new)
    for _ in range(5):assert '未完成' in match(c,'123','13800138000')
    assert '次数已达上限' in match(c,'123','13900139000')
    c.commit()
    assert hub.account(c,'123') is None

def test_scoped_review_credential_expires_and_cannot_issue_invites(c,client,monkeypatch):
    monkeypatch.setenv('MERCHANT_REVIEW_TOKEN','temporary-review-only')
    monkeypatch.setenv('MERCHANT_REVIEW_TOKEN_EXPIRES',str(int(time.time())+60))
    headers={'X-Service-Token':'temporary-review-only'}
    assert client.get('/merchant/ownership-requests',headers=headers).status_code==200
    assert client.post('/merchant/invitation',headers=headers).status_code==401
    monkeypatch.setenv('MERCHANT_REVIEW_TOKEN_EXPIRES','1')
    assert client.get('/merchant/ownership-requests',headers=headers).status_code==401


def test_one_tg_owner_can_switch_between_isolated_shops(c,tmp_path,monkeypatch):
    from scripts.create_test_shop import create, SHOP_NAME
    monkeypatch.setenv('MERCHANT_RUNTIME_DIR',str(tmp_path/'merchants'))
    hub.handle(c,'123','创建新档口')
    first=hub.account(c,'123')['id']
    c.commit()
    merchant,created=create('123')
    assert created and merchant['id']!=first
    c2=hub.connect()
    try:
        listing=hub.handle(c2,'123','我的档口')
        assert SHOP_NAME in listing and first[:8] in listing
        assert hub.account(c2,'123')['id']==merchant['id']
        assert '已切换' in hub.handle(c2,'123','切换档口 '+first[:8])
        assert hub.account(c2,'123')['id']==first
        assert hub.account(c2,'999') is None
    finally:c2.close()
