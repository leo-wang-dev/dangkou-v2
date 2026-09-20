import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('WECHAT_CUSTOMER_BOT_ENABLED','1')
    monkeypatch.setenv('WECHAT_MERCHANT_OWNER_IDS','owner-1')
    monkeypatch.setenv('WECHAT_CUSTOMER_STATE',str(tmp_path/'runtime'))
    c=db.connect(str(tmp_path/'shop.db'));db.init_db(c)
    c.execute("UPDATE shop_profile SET shop_name='人工开通测试店',owner_wechat='owner-wx' WHERE id=1");c.commit()
    from catalog import wechat_customer as mod
    monkeypatch.setattr(mod,'identity',lambda token:{'id':222222,'username':'test_customer_bot','is_bot':True})
    app=FastAPI();app.state.conn=c;app.state.token='service';mod.register(app)
    return c,mod,TestClient(app),tmp_path


def test_binding_and_secret_separation(env):
    c,m,client,tmp=env
    token='222222:'+('a'*35)
    r=client.post('/wechat/customer-bot/bind',headers={'X-Service-Token':'service'},json={'owner_id':'owner-1','token':token})
    assert r.status_code==200 and r.json()['runtime_status']=='pending'
    assert token not in r.text
    assert m.secret_path().stat().st_mode & 0o777 == 0o600
    assert c.execute('SELECT tg_bot_id FROM shop_profile').fetchone()[0]=='222222'
    assert token not in str(c.execute('SELECT * FROM shop_profile').fetchone())
    status=client.get('/wechat/customer-bot/status',headers={'X-Service-Token':'service'})
    assert status.json()['bot_url']=='https://t.me/test_customer_bot' and token not in status.text


def test_auth_and_bad_input_no_token_echo(env):
    c,m,client,tmp=env
    token='222222:'+('a'*35)
    for headers,body,status in [({}, {'owner_id':'owner-1','token':token},401),
        ({'X-Service-Token':'service'}, {'owner_id':'stranger','token':token},403),
        ({'X-Service-Token':'service'}, {'owner_id':'owner-1','token':{'value':token}},400)]:
        r=client.post('/wechat/customer-bot/bind',headers=headers,json=body)
        assert r.status_code==status and token not in r.text
    assert not m.secret_path().exists()


def test_other_bot_cannot_replace_existing(env):
    c,m,client,tmp=env
    c.execute("UPDATE shop_profile SET tg_bot_id='111111'");c.commit()
    r=client.post('/wechat/customer-bot/bind',headers={'X-Service-Token':'service'},json={'owner_id':'owner-1','token':'222222:'+('a'*35)})
    assert r.status_code==409 and not m.secret_path().exists()


def test_unapproved_seed_does_not_return_after_restart(env):
    c,m,client,tmp=env
    client.post('/wechat/customer-bot/bind',headers={'X-Service-Token':'service'},json={'owner_id':'owner-1','token':'222222:'+('a'*35)})
    db.init_db(c)
    from catalog import cs
    assert cs.get_redline(c)['text_raw']==''


def test_reserved_bot_rejected(env,monkeypatch):
    c,m,client,tmp=env;monkeypatch.setenv('WECHAT_RESERVED_TG_BOT_IDS','222222')
    r=client.post('/wechat/customer-bot/bind',headers={'X-Service-Token':'service'},json={'owner_id':'owner-1','token':'222222:'+('a'*35)})
    assert r.status_code==409 and not m.secret_path().exists()


def test_failed_commit_restores_previous_credentials(env):
    import json
    c,m,client,tmp=env
    m.write_secret(m.secret_path(),{'bot_id':'222222','bot_token':'222222:old'})
    class FailedCommit:
        def __getattr__(self,name):return getattr(c,name)
        def commit(self):raise RuntimeError('commit failure')
    client.app.state.conn=FailedCommit()
    with pytest.raises(RuntimeError,match='commit failure'):
        client.post('/wechat/customer-bot/bind',headers={'X-Service-Token':'service'},json={'owner_id':'owner-1','token':'222222:'+('a'*35)})
    assert json.loads(m.secret_path().read_text())['bot_token']=='222222:old'


def test_old_shop_ticket_cannot_manually_bind_bot(env):
    c,m,client,tmp=env
    from catalog import tickets,cs
    old=cs.get_shop(c)
    t=tickets.create(c,'shop',None,{'kind':'shop','changes':{'tg_bot_id':'222222','tg_bot_username':'forged_bot'},'old':old})
    m.activate(c);c.commit()
    with pytest.raises(tickets.TicketError):tickets.decide(c,t['id'],t['token'],True)
    assert c.execute('SELECT tg_bot_id FROM shop_profile').fetchone()[0]==''
