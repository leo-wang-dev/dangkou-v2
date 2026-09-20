"""Round 3: adversarial flow combinations and requirement gaps, offline only."""
import io
import json
import struct
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from catalog import cs, search, tickets
from catalog.storage import LocalStorage
from tests.test_release_gates import env, auth, photo


def test_followup_without_quantity_keeps_product_redline(env):
    conn, _, bot = env
    cs.set_redline(conn, 'p1', '仅本款：不接受月结')
    bot._on_text({'id':'a'}, 'MODEL-1')
    bot._on_text({'id':'a'}, '这款能月结吗')
    assert '仅本款：不接受月结' in bot.llm.chat_text.call_args.args[0]


def test_explicit_other_product_cannot_reuse_previous_price(env):
    _, _, bot = env
    bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')
    answer = bot._on_text({'id':'a'}, '另一款空气炸锅60个多少钱')
    assert 'MODEL-1' not in answer and '¥11' not in answer


def test_unknown_goods_go_to_merchant(env):
    conn, _, bot = env
    conn.execute("UPDATE shop_profile SET owner_tg_username='shop_owner',owner_wechat='shop-wx'")
    conn.commit()
    answer = bot._on_text({'id':'a'}, '有空气炸锅吗')
    assert 'shop-wx' in answer and '@shop_owner' in answer


def test_hidden_known_product_routes_to_owner_without_price(env):
    conn, _, bot = env
    conn.execute("UPDATE product_curler SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    answer = bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')
    assert '老板' in answer and '¥' not in answer
    assert conn.execute("SELECT COUNT(*) FROM cs_outbox WHERE channel='notify'").fetchone()[0] == 1


def test_exact_configured_price_not_rounded_by_output_format(env):
    conn, _, bot = env
    conn.execute("UPDATE product_curler SET tier_price='20:12345.67' WHERE id='p1'")
    conn.commit()
    assert '12345.67' not in bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')
    bot.llm.chat_text.assert_not_called()


def test_model_latency_does_not_quote_product_hidden_during_inference(env):
    conn, _, bot = env
    def hide_while_waiting(*args):
        conn.execute("UPDATE product_curler SET cs_visible=0 WHERE id='p1'")
        conn.commit()
        return '<<PASS>>'
    bot.llm.chat_text.side_effect = hide_while_waiting
    assert '¥' not in bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')


def test_poll_failure_does_not_stop_notification_delivery(env, monkeypatch):
    from scripts import run_cs_bot
    conn, _, _ = env
    conn.execute("UPDATE shop_profile SET shop_name='测试档口',tg_bot_id='12345'");conn.commit()
    api = Mock();api._call.return_value={'id':12345,'is_bot':True}
    api.poll.side_effect = [RuntimeError('TG offline'), KeyboardInterrupt()]
    bot = Mock()
    monkeypatch.setattr(run_cs_bot.db,'connect',lambda:conn)
    monkeypatch.setattr(run_cs_bot,'TgApi',lambda:api)
    monkeypatch.setattr(run_cs_bot,'CsBot',lambda *args:bot)
    monkeypatch.setattr(run_cs_bot.time,'sleep',lambda _:None)
    run_cs_bot.main()
    assert bot.flush_outbox.called


def test_first_hundred_poison_messages_do_not_starve_other_customer(env, monkeypatch):
    from scripts import run_cs_bot
    conn, _, _ = env
    for uid in range(1,102):
        payload = {'update_id':uid,'message':{'chat':{'id':1 if uid<=100 else 2}}}
        conn.execute('INSERT INTO cs_inbox(update_id,payload) VALUES(?,?)',(uid,json.dumps(payload)))
    conn.commit()
    conn.execute("UPDATE shop_profile SET shop_name='测试档口',tg_bot_id='12345'");conn.commit()
    api = Mock();api._call.return_value={'id':12345,'is_bot':True}
    api.poll.side_effect = [[], [], KeyboardInterrupt()]
    bot = Mock()
    bot.handle_update.side_effect = RuntimeError('poison')
    monkeypatch.setattr(run_cs_bot.db,'connect',lambda:conn)
    monkeypatch.setattr(run_cs_bot,'TgApi',lambda:api)
    monkeypatch.setattr(run_cs_bot,'CsBot',lambda *args:bot)
    monkeypatch.setattr(run_cs_bot.time,'sleep',lambda _:None)
    run_cs_bot.main()
    assert 101 in [c.args[0]['update_id'] for c in bot.handle_update.call_args_list]


def test_failed_image_approval_does_not_consume_ticket(env,tmp_path):
    conn, client, _ = env
    client.app.state.storage = LocalStorage(str(tmp_path))
    tk = client.patch('/products/curler/p1',headers=auth(),json={
        'changes':{'价格':'99'},'images':['_upload/missing.png']}).json()
    response = client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True})
    assert response.status_code >= 400
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(tk['ticket_id'],)).fetchone()[0] == 'pending'
    assert conn.execute("SELECT price FROM product_curler WHERE id='p1'").fetchone()[0] == '7.35'


def test_failed_direct_image_write_rolls_back_other_fields(env,tmp_path):
    conn, client, _ = env
    client.app.state.storage = LocalStorage(str(tmp_path))
    with TestClient(client.app,raise_server_exceptions=False) as api:
        response = api.patch('/products/curler/p1/direct',headers=auth(),json={
            'changes':{'price':'99'},'images':['../../outside.png']})
    assert response.status_code >= 400
    assert not conn.in_transaction
    assert conn.execute("SELECT price FROM product_curler WHERE id='p1'").fetchone()[0] == '7.35'


def test_two_images_swap_preserves_both_images(env,tmp_path,monkeypatch):
    conn, client, _ = env
    storage = client.app.state.storage = LocalStorage(str(tmp_path))
    red = storage.save('curler','p1','img0.png',b'RED')
    blue = storage.save('curler','p1','img1.png',b'BLUE')
    monkeypatch.setattr(search,'reindex',lambda *args:0)
    response = client.patch('/products/curler/p1/direct',headers=auth(),json={
        'changes':{},'images':[blue,red]})
    assert response.status_code == 200
    rels = json.loads(conn.execute("SELECT images FROM product_curler WHERE id='p1'").fetchone()[0])
    assert [storage.read(x) for x in rels] == [b'BLUE',b'RED']


def test_client_invalid_edit_body_is_rejected_not_server_error(env):
    _, client, _ = env
    with TestClient(client.app,raise_server_exceptions=False) as api:
        assert api.patch('/cs/link/link-a/note/1',json=[]).status_code in (400,422)


def test_upload_missing_file_is_rejected_not_server_error(env):
    _, client, _ = env
    with TestClient(client.app,raise_server_exceptions=False) as api:
        assert api.post('/upload',headers=auth(),data={}).status_code in (400,422)


def test_search_does_not_lose_live_hit_to_delisted_top_hit(env):
    conn, _, _ = env
    conn.execute("INSERT INTO product_curler(id,inner_code,item_no,status) VALUES('dead','dead','DEAD','delisted')")
    for pid, vector in [('dead',[1.,0.]),('p1',[.99,.01])]:
        conn.execute('INSERT INTO embedding(product_id,category,image_path,vec) VALUES(?,?,?,?)',
                     (pid,'curler','x',struct.pack('2f',*vector)))
    conn.commit()
    assert [h['product_id'] for h in search.query(conn,[1.,0.],top_k=1)] == ['p1']


def test_stock_stats_total_respects_requested_category(env):
    conn, client, _ = env
    conn.execute("INSERT INTO product_razor(id,inner_code,model_no) VALUES('r','r','RAZOR')")
    conn.commit()
    assert client.get('/stats?category=curler',headers=auth()).json()['total'] == 1


def test_import_notification_does_not_persist_service_secret(env,monkeypatch):
    from catalog import notify
    conn, _, _ = env
    monkeypatch.setattr(notify.config,'SERVICE_TOKEN','AUDIT-SECRET-DO-NOT-STORE')
    notify.push(1,1,'ticket-token',{'category':'curler','new':1},conn=conn)
    assert 'AUDIT-SECRET-DO-NOT-STORE' not in conn.execute('SELECT body FROM cs_outbox').fetchone()[0]


@pytest.mark.parametrize('changes',[
    {'items':[{'category':'curler','product_id':'p1','quantity':-1}]},
    {'items':[{'category':'curler','product_id':'p1','quantity':0}]},
    {'deposit_pct':101}, {'deposit_pct':-1}, {'price_adjustment_pct':-101},
])
def test_quote_rejects_invalid_business_numbers_before_generating(env,changes):
    _,client,_=env
    body={'items':[{'category':'curler','product_id':'p1','quantity':20}],**changes}
    assert client.post('/quote',headers=auth(),json=body).status_code in (400,422)


@pytest.mark.parametrize('kind',['missing','delisted'])
def test_quote_never_silently_omits_or_quotes_unavailable_product(env,tmp_path,kind):
    from catalog import quote
    conn,_,_=env
    if kind=='delisted':
        conn.execute("UPDATE product_curler SET status='delisted' WHERE id='p1'")
        conn.commit()
    items=[{'category':'curler','product_id':'p1' if kind=='delisted' else 'missing','quantity':20}]
    with pytest.raises(ValueError,match='商品'):
        quote.generate_v2(conn,LocalStorage(str(tmp_path)),items,0,str(tmp_path/'q.xlsx'))


def test_slow_model_request_does_not_block_other_database_requests(env,tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from catalog import db
    memory, client, _ = env
    disk = db.connect(str(tmp_path/'http-concurrency.db'))
    memory.backup(disk)
    client.app.state.conn = disk
    started, release = threading.Event(), threading.Event()
    def summarize(_):
        started.set()
        assert release.wait(4)
        return '数量少于20转人工'
    monkeypatch.setattr(cs,'_llm_summarize',summarize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(client.post,'/cs/redline',headers=auth(),json={'text_raw':'数量少于20转人工；'*100})
            assert started.wait(2)
            try:
                fast = pool.submit(client.get,'/products/curler',headers=auth())
                assert fast.result(timeout=1).status_code == 200
            finally:
                release.set()
            assert slow.result(timeout=2).status_code == 200
    finally:
        client.app.state.conn = memory
        disk.close()
