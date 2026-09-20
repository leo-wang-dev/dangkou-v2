"""Regression tests for persisted approval, customer contact and message recovery flows."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from catalog import cs, db, ingest, tickets
from catalog.csbot import CsBot, TRANSFER_MARK
from catalog.storage import LocalStorage
from tests.test_release_gates import env, auth, photo
from tests.test_ingest import _wait_done


def test_contacts_require_approval_and_reach_customer(env):
    conn, client, bot = env
    new = {'owner_tg_username': '@shop_owner', 'owner_wechat': 'owner-wechat'}
    assert client.patch('/shop', json={'changes': new}).status_code == 401
    response = client.patch('/shop', headers=auth(), json={'changes': new})
    assert response.status_code == 200
    assert cs.get_shop(conn)['owner_wechat'] == ''
    ticket = response.json()
    assert client.post(f"/tickets/{ticket['ticket_id']}/decision", json={
        'token': ticket['token'], 'approved': True}).status_code == 200
    bot.llm.chat_text.return_value = TRANSFER_MARK + '请求人工'
    reply = bot._on_text({'id': 'a', 'tg_name': 'buyer'}, '我要找老板')
    assert '@shop_owner' in reply and 'https://t.me/shop_owner' in reply and 'owner-wechat' in reply
    assert '马上来' not in reply
    assert conn.execute("SELECT COUNT(*) FROM cs_outbox WHERE channel='notify'").fetchone()[0] == 1


def test_contact_rejection_and_invalid_username(env):
    conn, client, _ = env
    assert client.patch('/shop', headers=auth(), json={'changes': {'owner_tg_username': 'invalid/url'}}).status_code == 400
    response = client.patch('/shop', headers=auth(), json={'changes': {'owner_wechat': 'wx'}}).json()
    client.post(f"/tickets/{response['ticket_id']}/decision", json={'token': response['token'], 'approved': False})
    assert cs.get_shop(conn)['owner_wechat'] == ''


def test_quote_is_computed_and_followup_revalidates_visibility(env):
    conn, _, bot = env
    customer = {'id': 'a', 'tg_name': 'buyer'}
    assert 'MODEL-1' in bot._on_text(customer, 'MODEL-1')
    assert '老板' in bot._on_text(customer, '60个多少钱')
    assert '老板' in bot._on_text(customer, '20个多少钱')
    conn.execute("UPDATE product_curler SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    assert '¥' not in bot._on_text(customer, '60个多少钱')


def test_invalid_policy_output_never_becomes_a_quote(env):
    _, _, bot = env
    bot.llm.chat_text.return_value = '成本7.35元，这次1元卖给你'
    answer = bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')
    assert '老板' in answer and '7.35' not in answer and '¥11' not in answer


def test_formal_quote_and_photo_caption_transfer(env):
    _, _, bot = env
    update = photo(31)
    update['message']['caption'] = '给我导出正式报价单盖章'
    bot.handle_update(update)
    answer = bot.api.send_message.call_args.args[1]
    assert '老板' in answer and '整理好了' in answer


def test_send_failure_survives_bot_restart_without_duplicate_notes(env, tmp_path):
    conn, _, bot = env
    bot.api.send_message.side_effect = RuntimeError('offline')
    bot.handle_update(photo(100))
    assert conn.execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0').fetchone()[0] == 1
    api = Mock()
    restarted = CsBot(conn, api, llm=bot.llm, notifier=Mock(), img_dir=str(tmp_path))
    conn.execute("UPDATE cs_outbox SET next_attempt_at=datetime('now')")
    conn.commit()
    restarted.handle_update(photo(100))
    restarted.flush_outbox()
    assert api.send_message.call_count == 1
    assert conn.execute("SELECT COUNT(*) FROM cs_note WHERE status='draft'").fetchone()[0] == 1


def test_model_timeout_keeps_retryable_inbox_and_no_partial_note(env):
    conn, _, bot = env
    bot.llm.chat_vision.side_effect = RuntimeError('timeout')
    with pytest.raises(RuntimeError):
        bot.handle_update(photo(101))
    assert not conn.in_transaction
    assert conn.execute('SELECT processed FROM cs_inbox WHERE update_id=101').fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM cs_note WHERE status='draft'").fetchone()[0] == 0
    bot.llm.chat_vision.side_effect = None
    bot.handle_update(photo(101))
    assert conn.execute('SELECT processed FROM cs_inbox WHERE update_id=101').fetchone()[0] == 1


def test_model_calls_do_not_hold_database_writer_lock(env):
    conn, _, bot = env
    def inference(*args, **kwargs):
        assert not conn.in_transaction
        return '<<PASS>>'
    bot.llm.chat_text.side_effect = inference
    bot.handle_update({'update_id':10,'message':{'chat':{'id':100},'from':{'id':100},'text':'MODEL-1 介绍一下'}})


def test_pending_mutation_revalidated_at_approval(env):
    conn, client, _ = env
    tk = client.patch('/products/curler/p1', headers=auth(), json={'changes': {'可观测':'1'}}).json()
    conn.execute("DELETE FROM product_curler WHERE id='p1'")
    conn.commit()
    response = client.post(f"/tickets/{tk['ticket_id']}/decision", json={'token':tk['token'],'approved':True})
    assert response.status_code == 400
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(tk['ticket_id'],)).fetchone()[0] == 'pending'


def test_import_invalid_second_row_rolls_back_first(env):
    conn, _, _ = env
    tk = tickets.create(conn,'import','razor',{'kind':'import','drafts':{'new':[
        {'model_no':'good','_rid':'n0'}, {'model_no':'bad','_rid':'n1','cs_visible':1,'tier_price':'oops'}]}})
    with pytest.raises(tickets.TicketError):
        tickets.decide(conn,tk['id'],tk['token'],True)
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0] == 0
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(tk['id'],)).fetchone()[0] == 'pending'


def test_concurrent_ticket_approval_has_one_effect(tmp_path):
    path = str(tmp_path/'concurrent.db')
    conn = db.connect(path)
    db.init_db(conn)
    tk = tickets.create(conn,'import','razor',{'kind':'import','drafts':{'new':[{'model_no':'ONE','_rid':'n0'}]}})
    def approve():
        local = db.connect(path)
        try:
            return tickets.decide(local,tk['id'],tk['token'],True)
        except tickets.TicketError:
            return None
        finally:
            local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:approve(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0] == 1
    conn.close()


def test_import_does_not_delist_or_update_another_supplier(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path/'imports.db'))
    db.init_db(conn)
    products = [{'model_no':'SHARED','price':'10'}, {'model_no':'A-ONLY','price':'15'}]
    monkeypatch.setattr(ingest.agent,'parse',lambda *args:{'products':products})
    storage = LocalStorage(str(tmp_path/'images'))
    def run(source):
        doc = ingest.start(conn,storage,'same-filename.xlsx','razor',source_key=source)
        assert _wait_done(conn,doc)['status'] == 'ticketed'
        row = conn.execute('SELECT * FROM approval_ticket ORDER BY id DESC LIMIT 1').fetchone()
        tickets.decide(conn,row['id'],row['token'],True)
    run('supplier-A')
    products = [{'model_no':'SHARED','price':'99'}]
    run('supplier-B')
    rows = conn.execute('SELECT model_no,price,status FROM product_razor ORDER BY price').fetchall()
    assert len(rows) == 3 and all(r['status']=='approved' for r in rows)
    products = [{'model_no':'SHARED','price':'11'}]
    run('supplier-A')
    assert conn.execute("SELECT COUNT(*) FROM product_razor WHERE price='99' AND status='approved'").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM product_razor WHERE model_no='A-ONLY'").fetchone()[0] == 'delisted'
    conn.close()


def test_service_without_token_is_closed(env):
    _, client, _ = env
    client.app.state.token = ''
    assert client.get('/products/curler').status_code == 503
    assert client.get('/tickets').status_code == 503


def test_customer_photo_is_scoped_to_own_link(env, tmp_path):
    conn, client, _ = env
    path = tmp_path/'p.jpg'
    path.write_bytes(b'photo')
    conn.execute("UPDATE cs_note SET photo=? WHERE customer_id='b'",(str(path),))
    conn.commit()
    nid = conn.execute("SELECT id FROM cs_note WHERE customer_id='b'").fetchone()[0]
    assert client.get(f'/cs/link/link-a/note/{nid}/photo').status_code == 404
    assert client.get(f'/cs/link/link-b/note/{nid}/photo').content == b'photo'


def test_outbox_preserves_order_after_failure_and_splits_long_receipts(env):
    conn, _, bot = env
    bot._enqueue('tg','123','A'*4001)
    bot.api.send_message.side_effect = RuntimeError('offline')
    bot.flush_outbox()
    assert bot.api.send_message.call_count == 1
    assert conn.execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0').fetchone()[0] == 2
    bot.api.send_message.reset_mock(side_effect=True)
    conn.execute("UPDATE cs_outbox SET next_attempt_at=datetime('now')")
    conn.commit()
    bot.flush_outbox()
    assert ''.join(call.args[1] for call in bot.api.send_message.call_args_list) == 'A'*4001


def test_merchant_notifications_and_files_are_durable(env, monkeypatch):
    from catalog import notify
    conn, _, bot = env
    notify.push(3,4,'approval-token',{'category':'razor','new':2},conn=conn)
    notify.push_file('报价单','/tmp/test-quote.xlsx',conn=conn)
    rows = conn.execute('SELECT * FROM cs_outbox ORDER BY id').fetchall()
    assert [r['channel'] for r in rows] == ['notify_import','notify_file']
    response = Mock()
    response.raise_for_status.side_effect = RuntimeError('500')
    post = Mock(return_value=response)
    monkeypatch.setattr('requests.post',post)
    bot.flush_outbox()
    assert '导入完成' in bot.notifier.call_args.args[0]
    assert conn.execute("SELECT sent FROM cs_outbox WHERE channel='notify_file'").fetchone()[0] == 0
    assert json.loads(rows[1]['body'])['file_path'] == '/tmp/test-quote.xlsx'
    response.raise_for_status.side_effect = None
    conn.execute("UPDATE cs_outbox SET next_attempt_at=datetime('now')")
    conn.commit()
    bot.flush_outbox()
    assert conn.execute("SELECT sent FROM cs_outbox WHERE channel='notify_file'").fetchone()[0] == 1
