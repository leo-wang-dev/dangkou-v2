"""Offline release-gate probes. Assertions describe required behavior, not current bugs.

Run separately: .venv/bin/python -m pytest audit/test_release_gates.py -q
No real customers, external model calls or production databases are used.
"""
import io
import json
import sqlite3
from unittest.mock import Mock

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from catalog import cs, db, tickets
from catalog.api import register_routes
from catalog.csbot import CsBot


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_NOTIFY_TOKEN', raising=False)
    # A stray external request must fail immediately in this offline audit.
    monkeypatch.setattr('requests.sessions.Session.request',
                        Mock(side_effect=AssertionError('external request forbidden')))
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'audit-service-secret'
    app.state.storage = None
    app.state.callback = None
    register_routes(app)
    conn.execute("INSERT INTO product_curler(id,inner_code,item_no,price,tier_price,cs_visible) "
                 "VALUES('p1','AUDIT-1','MODEL-1','7.35','20:12;50:11',1)")
    for cid in ('a', 'b'):
        conn.execute('INSERT INTO cs_customer(id,tg_id) VALUES(?,?)', (cid, cid))
        conn.execute("INSERT INTO cs_note(customer_id,fields_json,status) VALUES(?,?,'confirmed')",
                     (cid, json.dumps({'价格': '12'})))
        conn.execute("INSERT INTO cs_link(token,customer_id,expires_at) "
                     "VALUES(?,?,datetime('now','+2 days'))", ('link-' + cid, cid))
    conn.execute("INSERT INTO cs_link(token,customer_id,expires_at) "
                 "VALUES('expired','a',datetime('now','-1 day'))")
    conn.commit()
    api = Mock()
    img = io.BytesIO()
    Image.new('RGB', (20, 20), 'red').save(img, format='JPEG')
    api.download_photo.return_value = img.getvalue()
    llm = Mock()
    llm.chat_vision.return_value = '[{"型号或品名":"A","价格":"12"}]'
    llm.chat_text.return_value = '<<PASS>>'
    bot = CsBot(conn, api, llm=llm, notifier=Mock(), img_dir=str(tmp_path))
    # 新版客户 bot 首条消息先问语言；离线审计里的买家 tg_id 固定 100/200，
    # 预置“中文”让用例直接进入业务分支。
    from catalog import cs_i18n
    for tg in (100, 200):
        bot._ensure_customer({'id': tg})
        cs_i18n.set_language(
            conn, conn.execute("SELECT id FROM cs_customer WHERE tg_id=?", (str(tg),)).fetchone()[0],
            '中文')
    conn.commit()
    with TestClient(app) as client:
        yield conn, client, bot
    conn.close()


def auth():
    return {'X-Service-Token': 'audit-service-secret'}


def photo(update_id=1):
    return {'update_id': update_id, 'message': {
        'chat': {'id': 100, 'type': 'private'}, 'from': {'id': 100},
        'photo': [{'file_id': 'photo', 'width': 20, 'height': 20}]}}


def test_unauthenticated_ticket_cannot_be_approved(env):
    conn, client, _ = env
    tk = tickets.create(conn, 'redline', None,
                        {'kind': 'redline', 'text_raw': 'AUDIT-CHANGED'})
    response = client.get('/tickets')
    if response.status_code in (401, 403):
        return
    exposed = next(t for t in response.json()['tickets'] if t['id'] == tk['id'])
    response = client.post(f"/tickets/{tk['id']}/decision",
                           json={'token': exposed['token'], 'approved': True})
    assert response.status_code in (401, 403), 'anonymous reader obtained approval token and approved ticket'


@pytest.mark.parametrize('path', ['/products/curler', '/stats?full=true'])
def test_internal_cost_not_public(env, path):
    _, client, _ = env
    response = client.get(path)
    assert response.status_code in (401, 403) or '7.35' not in response.text


def test_link_cannot_edit_another_customer_note(env):
    conn, client, _ = env
    nid = conn.execute("SELECT id FROM cs_note WHERE customer_id='b'").fetchone()['id']
    response = client.patch(f'/cs/link/link-a/note/{nid}', json={'field': '价格', 'value': '0.01'})
    assert response.status_code in (403, 404), 'customer a changed customer b note'


@pytest.mark.parametrize('operation', ['read', 'edit', 'export'])
def test_expired_link_denied(env, operation):
    conn, client, _ = env
    nid = conn.execute("SELECT id FROM cs_note WHERE customer_id='a'").fetchone()['id']
    if operation == 'edit':
        response = client.patch(f'/cs/link/expired/note/{nid}', json={'field': '价格', 'value': '5'})
    else:
        response = client.get('/cs/link/expired' + ('/export.xlsx' if operation == 'export' else ''))
    assert response.status_code in (403, 404, 410), f'expired link allowed {operation}'


def test_export_contains_real_photo(env, tmp_path):
    conn, client, _ = env
    path = tmp_path / 'sample.jpg'
    Image.new('RGB', (20, 20), 'red').save(path)
    conn.execute("UPDATE cs_note SET photo=? WHERE customer_id='a'", (str(path),))
    conn.commit()
    response = client.get('/cs/link/link-a/export.xlsx')
    assert response.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    assert len(wb.active._images) == 1, 'valid photo omitted from normal extraction fields'


def test_product_redline_reaches_persona(env):
    conn, _, bot = env
    cs.set_redline(conn, 'p1', 'MODEL-1独有红线：少于88个转人工')
    bot._on_text({'id': 'a', 'tg_name': 'audit'}, 'MODEL-1')
    system = bot.llm.chat_text.call_args.args[0]
    assert '少于88个转人工' in system, 'product-specific policy missing from actual prompt'


def test_persona_quote_uses_code_tier_selection(env, monkeypatch):
    _, _, bot = env
    answer=bot._on_text({'id':'a'},'MODEL-1 60个多少钱')
    assert '老板' not in answer and '¥' not in answer
    assert not hasattr(cs,'pick_tier')
    assert bot.llm.chat_text.call_count == 1


def test_visible_product_cannot_lose_valid_tiers(env):
    _, client, _ = env
    response=client.patch('/products/curler/p1',headers=auth(),json={'changes':{'阶梯价':'20:12'}})
    assert response.status_code==400


def test_long_redline_is_compressed_through_api(env, monkeypatch):
    conn, client, _ = env
    spy = Mock(return_value='压缩结果')
    monkeypatch.setattr(cs, '_llm_summarize', spy)
    response = client.post('/cs/redline', headers=auth(), json={'text_raw': '数量少于50个转人工；' * 100})
    assert response.status_code == 200
    assert spy.called, 'API uses summarize default llm=False'


def test_second_photo_receipt_indexes_all_drafts(env):
    _, _, bot = env
    bot.handle_update(photo(1))
    bot.llm.chat_vision.return_value = '[{"型号或品名":"B","价格":"13"}]'
    bot.handle_update(photo(2))
    receipt = bot.api.send_message.call_args.args[1]
    assert '【2】' in receipt, 'second photo is labeled 1 although editing 1 targets first photo'


def test_duplicate_update_is_idempotent(env):
    conn, _, bot = env
    bot.handle_update(photo(1))
    bot.handle_update(photo(1))
    count = conn.execute("SELECT COUNT(*) FROM cs_note WHERE status='draft'").fetchone()[0]
    assert count == 1, 'same Telegram update produced duplicate notes'


def test_customer_links_still_read_own_notes(env):
    _, client, _ = env
    response = client.get('/cs/link/link-a')
    assert response.status_code == 200
    assert 'customer_id' not in response.json()
    assert len(response.json()['notes']) == 1


def test_service_write_auth_is_enabled(env):
    _, client, _ = env
    assert client.patch('/products/curler/p1', json={'changes': {'价格': '1'}}).status_code == 401


def test_failed_update_does_not_discard_rest_of_batch(env, monkeypatch):
    from scripts import run_cs_bot
    from catalog.tg import TgApi
    conn, _, _ = env
    api = TgApi(token='offline-audit')
    first, second = {'update_id': 1}, {'update_id': 2}
    conn.execute("UPDATE shop_profile SET shop_name='测试档口',tg_bot_id='12345'");conn.commit()
    api._call = Mock(side_effect=[{'id':12345,'is_bot':True}, [first, second], KeyboardInterrupt()])
    bot = Mock()
    bot.handle_update.side_effect = [RuntimeError('simulated model timeout'), None]
    monkeypatch.setattr(run_cs_bot.db, 'connect', lambda: conn)
    monkeypatch.setattr(run_cs_bot, 'TgApi', lambda: api)
    monkeypatch.setattr(run_cs_bot, 'CsBot', lambda *a: bot)
    monkeypatch.setattr(run_cs_bot.time, 'sleep', lambda _: None)
    run_cs_bot.main()
    handled = [call.args[0]['update_id'] for call in bot.handle_update.call_args_list]
    assert 2 in handled, f'second update skipped; next poll already acknowledges offset {api._offset}'


def test_wechat_http_failure_is_detected(env, monkeypatch, capsys):
    response = Mock(status_code=500)
    response.raise_for_status.side_effect = RuntimeError('500 notification rejected')
    monkeypatch.setattr('requests.post', Mock(return_value=response))
    CsBot._wechat_remind('offline audit notice')
    output = capsys.readouterr().out
    assert response.raise_for_status.called or '失败' in output, 'HTTP 500 is silently treated as successful notification'
