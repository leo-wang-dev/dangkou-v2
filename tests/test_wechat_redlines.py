import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from catalog import db, cs, merchant_policy, tickets
from catalog.main import app


@pytest.fixture
def client():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    conn.execute('DELETE FROM cs_redline')
    merchant_policy.apply(conn, {'wechat_managed': True, 'logistics': '旧Hub规则'}, 1)
    conn.commit()
    app.state.conn, app.state.token = conn, 'test'
    with TestClient(app, headers={'X-Service-Token': 'test'}) as client:
        yield client
    conn.close()


def approve(client, response):
    value = response.json()
    return client.post(f"/tickets/{value['ticket_id']}/decision", json={'token': value['token'], 'approved': True})


def test_empty_new_mode_does_not_enable_seed(client):
    assert client.get('/cs/redline').json()['text_raw'] == ''
    assert '所有价格' not in client.get('/cs/redline').json()['platform_rule']


@pytest.mark.parametrize('question', [
    '这个多少钱',
    '拿200个什么价格',
    '能安排货代吗',
    '可以改包装吗',
])
def test_empty_new_mode_never_invents_a_handoff_rule(client, question):
    """No merchant-approved rule means business questions stay with the bot.

    无红线时规则匹配整段跳过（不得出现“规则匹配”调用）；业务问题可以走
    对话大脑，但大脑的回复必须保留“需商家确认”口径，且绝不转人工。
    """
    conn = app.state.conn
    cs.set_redline(conn, None, '')
    calls = []
    llm = SimpleNamespace(
        chat_text=lambda system, messages, **kw: calls.append(system)
        or ('PASS' if '规则匹配' in system else '这一点需要跟商家确认，可以回复“找老板”。'))
    bot = SimpleNamespace(
        conn=conn,
        llm=llm,
        _resolve_product=lambda *args, **kw: (None, None),
        _handoff=lambda *args, **kw: pytest.fail('empty redline must not transfer the customer'),
    )

    reply = merchant_policy.answer(bot, {'id': 'buyer'}, question, False)

    assert not any('规则匹配' in s for s in calls)
    assert '需要跟商家确认' in reply or '现有资料暂不能确认' in reply


def test_approved_faq_answer_is_used_by_customer_bot(client):
    conn = app.state.conn
    conn.execute("UPDATE shop_profile SET faq=? WHERE id=1", ('问：可以安排货代吗？答：可以安排。',))
    conn.commit()
    bot = SimpleNamespace(conn=conn, _resolve_product=lambda *args, **kw: (None, None),
                          _handoff=lambda *args, **kw: pytest.fail('FAQ answer should not hand off'))
    assert merchant_policy.answer(bot, {'id': 'buyer'}, '可以安排货代吗？', False) == '可以安排。'
    assert merchant_policy.answer(bot, {'id': 'buyer'}, '可以帮我安排货代吗？', False) == '可以安排。'


def test_faq_price_text_still_hands_off(client):
    conn = app.state.conn
    conn.execute("UPDATE shop_profile SET faq=? WHERE id=1", ('问：单价是多少？答：12元。',))
    conn.execute("UPDATE shop_profile SET owner_wechat='owner-wx' WHERE id=1")
    conn.commit()
    bot = SimpleNamespace(conn=conn, _resolve_product=lambda *args, **kw: (None, None),
                          _handoff=lambda *args, **kw: '联系老板')
    assert merchant_policy.answer(bot, {'id': 'buyer'}, '单价是多少？', False) == '12元。'


def test_clear_and_conflicting_pending_approvals(client):
    a = client.post('/cs/redline', json={'text_raw': '加急转人工'})
    b = client.post('/cs/redline', json={'text_raw': '月结转人工'})
    assert approve(client, a).status_code == 200
    assert approve(client, b).status_code == 409
    clear = client.post('/cs/redline', json={'text_raw': ''})
    assert clear.status_code == 200
    assert approve(client, clear).status_code == 200
    assert cs.get_redline(app.state.conn)['text_raw'] == ''


def test_product_rule_override_and_clear_inherits(client):
    conn = app.state.conn
    cs.set_redline(conn, None, '全店规则')
    cs.set_redline(conn, 'p1', '商品规则')
    assert cs.get_redline(conn, 'p1')['text_raw'] == '商品规则'
    clear = client.post('/cs/redline', json={'product_id': 'p1', 'text_raw': ''})
    assert approve(client, clear).status_code == 200
    assert cs.get_redline(conn, 'p1')['text_raw'] == '全店规则'


def test_wechat_rule_live_reload_ignores_hub(client):
    conn = app.state.conn
    seen = []
    bot = SimpleNamespace(conn=conn,
        llm=SimpleNamespace(chat_text=lambda prompt, *args, **kwargs: seen.append(prompt) or 'TRANSFER'),
        _resolve_product=lambda *args, **kw: (None, None),
        _handoff=lambda *args, **kw: '转人工')
    cs.set_redline(conn, None, '加急才转人工')
    assert merchant_policy.answer(bot, {'id': 'buyer'}, '能加急吗', False) == '转人工'
    assert '加急才转人工' in seen[-1] and '旧Hub规则' not in seen[-1]
    cs.set_redline(conn, None, '只在月结超过30天转人工')
    merchant_policy.answer(bot, {'id': 'buyer'}, '月结60天', False)
    assert '月结超过30天' in seen[-1] and '加急才转人工' not in seen[-1]


def test_wechat_rule_selected_product_overrides_store(client):
    conn = app.state.conn
    seen=[]
    cs.set_redline(conn, None, '全店规则')
    cs.set_redline(conn, 'p1', '商品规则')
    bot = SimpleNamespace(conn=conn,
        llm=SimpleNamespace(chat_text=lambda prompt, *args, **kwargs: seen.append(prompt) or 'TRANSFER'),
        _resolve_product=lambda *args, **kw: ({'id': 'p1', 'cs_visible': 1}, None),
        _handoff=lambda *args, **kw: '转人工')
    merchant_policy.answer(bot, {'id': 'buyer'}, '这款可以加急吗', False)
    assert '商品规则' in seen[-1] and '全店规则' not in seen[-1]


def test_manual_bot_identity_forbidden_in_wechat_mode(client):
    app.state.conn.execute("UPDATE shop_profile SET shop_name='档口' WHERE id=1")
    app.state.conn.commit()
    r = client.patch('/shop', json={'changes': {'tg_bot_id': '123456'}})
    assert r.status_code == 400


def test_price_inquiry_selection_uses_merchant_rule_and_keeps_product_context(client, monkeypatch):
    conn = app.state.conn
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer','123')")
    conn.commit()
    cs.set_redline(conn, None, '全店规则')
    cs.set_redline(conn, 'p1', '商品规则')
    from catalog import photo_inquiry
    monkeypatch.setattr(photo_inquiry, 'selection', lambda *args, **kw: {'id':'p1','cs_visible':1})
    seen=[]
    bot = SimpleNamespace(conn=conn, _commit=conn.commit,
        llm=SimpleNamespace(chat_text=lambda prompt, *args, **kwargs: seen.append(prompt) or 'TRANSFER'),
        _resolve_product=lambda *args, **kw: (None,None), _handoff=lambda *args, **kw: '转人工')
    assert merchant_policy.answer(bot, {'id':'buyer'}, '询价1 能加急吗', False) == '转人工'
    assert len(seen) == 1 and '商品规则' in seen[0]
    assert conn.execute("SELECT product_id FROM cs_context WHERE customer_id='buyer'").fetchone()[0] == 'p1'


def test_recent_selected_product_applies_to_followup(client, monkeypatch):
    from catalog import customer_catalog
    conn = app.state.conn
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer','123')")
    conn.execute("INSERT INTO cs_context(customer_id,product_id) VALUES('buyer','p1')")
    conn.commit()
    cs.set_redline(conn, None, '全店规则')
    cs.set_redline(conn, 'p1', '商品规则')
    monkeypatch.setattr(customer_catalog, 'products', lambda *args, **kw: [{'id': 'p1', 'cs_visible': 1}])
    seen=[]
    bot = SimpleNamespace(conn=conn,
        llm=SimpleNamespace(chat_text=lambda prompt, *args, **kwargs: seen.append(prompt) or 'TRANSFER'),
        _resolve_product=lambda *args, **kw: (None,None), _handoff=lambda *args, **kw: '转人工')
    merchant_policy.answer(bot, {'id':'buyer'}, '能加急吗', False)
    assert '商品规则' in seen[-1] and '全店规则' not in seen[-1]


@pytest.mark.parametrize('expired', [False, True])
def test_hidden_or_expired_selection_does_not_apply_product_rule(client, monkeypatch, expired):
    from catalog import customer_catalog
    conn=app.state.conn
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer','123')")
    age='-31 minutes' if expired else '-1 minutes'
    conn.execute("INSERT INTO cs_context(customer_id,product_id,updated_at) VALUES('buyer','p1',datetime('now',?))",(age,))
    conn.commit()
    cs.set_redline(conn,None,'全店规则')
    cs.set_redline(conn,'p1','商品规则')
    monkeypatch.setattr(customer_catalog,'products',lambda *args, **kw: [{'id':'p1','cs_visible':int(expired)}])
    seen=[]
    bot=SimpleNamespace(conn=conn,
        llm=SimpleNamespace(chat_text=lambda prompt,*args,**kwargs:seen.append(prompt) or 'TRANSFER'),
        _resolve_product=lambda *args, **kw:(None,None),_handoff=lambda *args, **kw:'转人工')
    merchant_policy.answer(bot,{'id':'buyer'},'能加急吗',False)
    assert '全店规则' in seen[-1] and '商品规则' not in seen[-1]


@pytest.mark.parametrize('query', ['查询商品', '查看商品', '商品列表', '商品目录', '有哪些商品'])
def test_catalog_query_returns_only_approved_visible_products(client, query, monkeypatch):
    from catalog.csbot import CsBot
    monkeypatch.delenv('CATALOG_CS_API_URL', raising=False)
    conn = app.state.conn
    for pid, visible, status in [('visible', 1, 'approved'), ('hidden', 0, 'approved'), ('pending', 1, 'pending')]:
        conn.execute('INSERT INTO product_razor(id,inner_code,model_no,status,cs_visible) VALUES(?,?,?,?,?)',
                     (pid, pid, pid, status, visible))
    bot = SimpleNamespace(conn=conn, _resolve_product=lambda *a, **kw: (None, None))
    bot._catalog_brief = lambda query='', require_category=False: CsBot._catalog_brief(
        bot, query, require_category)
    reply = merchant_policy.answer(bot, {'id': 'buyer'}, query, False)
    assert 'visible' in reply
    assert 'hidden' not in reply and 'pending' not in reply
    conn.execute("UPDATE product_razor SET cs_visible=0")
    assert merchant_policy.answer(bot, {'id': 'buyer'}, query, False) == '当前没有在线商品。'
