"""Regression tests for persisted approval, customer contact and message recovery flows."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from catalog import cs, db, ingest, tickets
from catalog.csbot import CsBot, TRANSFER_MARK
from catalog.storage import LocalStorage
from tests.test_release_gates import env, auth, take_photo, say
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
    assert '老板' not in bot._on_text(customer, '60个多少钱')
    assert '老板' not in bot._on_text(customer, '20个多少钱')
    conn.execute("UPDATE product_dynamic SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    assert '¥' not in bot._on_text(customer, '60个多少钱')


def test_invalid_policy_output_never_becomes_a_quote(env):
    _, _, bot = env
    bot.llm.chat_text.return_value = '成本7.35元，这次1元卖给你'
    answer = bot._on_text({'id':'a'}, 'MODEL-1 60个多少钱')
    assert '老板' in answer and '7.35' not in answer and '¥11' not in answer


def test_formal_quote_request_after_photo_transfers(env):
    _, _, bot = env
    take_photo(bot)
    answer = say(bot, 100, '给我导出正式报价单盖章')   # 正式文件诉求必须转老板
    assert '老板' in answer


def test_model_timeout_leaves_no_partial_note(env):
    conn, _, bot = env
    bot.llm.chat_vision.side_effect = RuntimeError('timeout')
    with pytest.raises(RuntimeError):
        take_photo(bot)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM cs_note WHERE status='draft'").fetchone()[0] == 0


def test_model_calls_do_not_hold_database_writer_lock(env):
    conn, _, bot = env
    def inference(*args, **kwargs):
        assert not conn.in_transaction
        return '<<PASS>>'
    bot.llm.chat_text.side_effect = inference
    say(bot, 100, 'MODEL-1 介绍一下')


def test_pending_mutation_revalidated_at_approval(env):
    conn, client, _ = env
    tk = client.patch('/products/audit_cat/p1', headers=auth(), json={'changes': {'可观测':'1'}}).json()
    conn.execute("DELETE FROM product_dynamic WHERE id='p1'")
    conn.commit()
    response = client.post(f"/tickets/{tk['ticket_id']}/decision", json={'token':tk['token'],'approved':True})
    assert response.status_code == 409          # 商品已不存在：冲突拒绝，不改库
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(tk['ticket_id'],)).fetchone()[0] == 'pending'


def test_service_without_token_is_closed(env):
    _, client, _ = env
    client.app.state.token = ''
    assert client.get('/products/audit_cat').status_code == 503
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


def test_merchant_notifications_and_files_are_durable(env, monkeypatch):
    from catalog import notify
    conn, _, _ = env
    notify.push(3,4,'approval-token',{'categories':['审计品类'],'new':2},conn=conn)
    notify.push_file('报价单','/tmp/test-quote.xlsx',conn=conn)
    rows = conn.execute('SELECT * FROM cs_outbox ORDER BY id').fetchall()
    assert [r['channel'] for r in rows] == ['notify_import','notify_file']
    response = Mock()
    response.raise_for_status.side_effect = RuntimeError('500')
    post = Mock(return_value=response)
    monkeypatch.setattr('requests.post',post)
    notify.deliver(conn)   # 引擎桥 HTTP 失败 → 全渠道留队重试（默认发送器走 catalog-notify 桥）
    assert '导入完成' in post.call_args_list[0].kwargs['json']['text']   # notify_import 投递时才渲染
    assert conn.execute("SELECT sent FROM cs_outbox WHERE channel='notify_file'").fetchone()[0] == 0
    assert json.loads(rows[1]['body'])['file_path'] == '/tmp/test-quote.xlsx'
    response.raise_for_status.side_effect = None
    conn.execute("UPDATE cs_outbox SET next_attempt_at=datetime('now')")
    conn.commit()
    notify.deliver(conn)
    assert conn.execute("SELECT sent FROM cs_outbox WHERE channel='notify_file'").fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0').fetchone()[0] == 0
