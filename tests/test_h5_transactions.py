"""File-backed H5 requests must expose only complete, durable business turns."""
import io
import json
import sqlite3
import asyncio
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from catalog import db, llm, merchant_policy, userapp
from catalog.api import register_routes
from catalog.storage import LocalStorage


@pytest.fixture()
def h5(tmp_path, monkeypatch):
    database = tmp_path / 'catalog.db'
    monkeypatch.setenv('CATALOG_CS_PHOTOS', str(tmp_path / 'photos'))
    monkeypatch.delenv('CATALOG_CS_API_URL', raising=False)
    conn = db.connect(str(database))
    db.init_db(conn)
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    conn.execute("UPDATE shop_profile SET chat_token='test-shop', owner_wechat='boss'")
    conn.commit()
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'test-token'
    app.state.storage = LocalStorage(str(tmp_path / 'images'))
    app.state.callback = None
    from catalog import guest_sessions
    app.state.test_guests = {label:guest_sessions.issue(conn) for label in ['bad', 'bad-language', 'busy', 'conflict', 'failure', 'good', 'one', 'replay-failure', 'two','slow','other','same','cancelled']}
    conn.commit()
    register_routes(app)
    monkeypatch.setattr(llm, 'chat_vision', lambda *a, **kw: json.dumps(
        [{'型号或品名': 'SAMPLE-1', '颜色': '黑色'}], ensure_ascii=False))
    monkeypatch.setattr(llm, 'chat_text', lambda *a, **kw: '{"actions":[]}')
    photo = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(photo, 'JPEG')
    yield app, database, photo.getvalue()
    conn.close()


def _count(database, table):
    with sqlite3.connect(database) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_successful_photo_commits_note_candidates_and_log(h5):
    app, database, photo = h5
    with TestClient(app) as client:
        client.post('/cs/chat/test-shop/mode',json={'visitor':app.state.test_guests['one'],'mode':'notes'})
        response = client.post('/cs/chat/test-shop/photo', data={'visitor': app.state.test_guests['one']},
                               files={'file': ('p.jpg', photo, 'image/jpeg')})
    assert response.status_code == 200
    assert '整理好了' in response.json()['reply']
    assert _count(database, 'cs_customer') == 1
    assert _count(database, 'cs_note') == 1
    assert _count(database, 'cs_photo_candidates') == 1
    assert _count(database, 'cs_conversation_log') == 1


def test_successful_boss_handoff_commits_outbox(h5):
    app, database, _ = h5
    with TestClient(app) as client:
        response = client.post('/cs/chat/test-shop/message',
                               json={'visitor': app.state.test_guests['two'], 'text': '找老板'})
    assert response.status_code == 200
    assert _count(database, 'cs_outbox') == 1
    assert _count(database, 'cs_conversation_log') >= 1


def test_failed_turn_rolls_back_visitor_and_log(h5, monkeypatch):
    from catalog.cs_chat import H5Bot

    app, database, _ = h5

    def fail_after_log(self, cust, text):
        self._log(cust['id'], 'user', text)
        raise RuntimeError('failed after writing')

    monkeypatch.setattr(H5Bot, '_text_turn', fail_after_log)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/cs/chat/test-shop/message',
                               json={'visitor': app.state.test_guests['failure'], 'text': 'hello'})
    assert response.status_code == 500
    assert _count(database, 'cs_customer') == 0
    assert _count(database, 'cs_conversation_log') == 0


def test_failed_turn_does_not_undo_another_visitors_success(h5, monkeypatch):
    from catalog.cs_chat import H5Bot

    app, database, _ = h5
    with TestClient(app) as client:
        good = client.post('/cs/chat/test-shop/message',
                           json={'visitor': app.state.test_guests['good'], 'text': '找老板'})
        assert good.status_code == 200

    def fail_after_log(self, cust, text):
        self._log(cust['id'], 'user', text)
        raise RuntimeError('failed after writing')

    monkeypatch.setattr(H5Bot, '_text_turn', fail_after_log)
    with TestClient(app, raise_server_exceptions=False) as client:
        bad = client.post('/cs/chat/test-shop/message',
                          json={'visitor': app.state.test_guests['bad'], 'text': 'hello'})
    assert bad.status_code == 500
    assert _count(database, 'cs_customer') == 1
    assert _count(database, 'cs_outbox') == 1


def test_userapp_failed_photo_is_not_committed_by_later_request(tmp_path, monkeypatch):
    class Vision:
        def chat_vision(self, *args, **kwargs):
            return json.dumps([{'型号或品名': 'GOOD'}, {'型号或品名': 'BROKEN'}],
                              ensure_ascii=False)

    from catalog import userapp as userapp_module
    original = userapp_module.normalize_fields

    def normalize(fields):
        if fields.get('型号或品名') == 'BROKEN':
            raise RuntimeError('failed after first note')
        return original(fields)

    monkeypatch.setattr(userapp_module, 'normalize_fields', normalize)
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    database = tmp_path / 'user.db'
    app = userapp.build_app(db_path=str(database), photo_dir=str(tmp_path / 'photos'),
                            codes_log=str(tmp_path / 'codes.log'), llm=Vision())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/photo', data={'owner': client.post('/guest').json()['guest']},
                               files={'file': ('p.jpg', b'photo', 'image/jpeg')})
        later = client.post('/auth/code', json={'email': 'buyer@example.com'})
    assert response.status_code == 500
    assert later.status_code == 200
    assert _count(database, 'notes') == 0


def test_text_replay_failure_rolls_back_visitor_and_log(h5, monkeypatch):
    from catalog.cs_chat import H5Bot

    app, database, _ = h5
    original = H5Bot._text_turn

    def fail_on_real_connection(self, cust, text):
        if self.conn.execute('PRAGMA database_list').fetchone()[2]:
            self._log(cust['id'], 'user', text)
            raise RuntimeError('apply failed after logging')
        return original(self, cust, text)

    monkeypatch.setattr(H5Bot, '_text_turn', fail_on_real_connection)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/cs/chat/test-shop/message',
                               json={'visitor': app.state.test_guests['replay-failure'], 'text': '找老板'})
    assert response.status_code == 500
    assert _count(database, 'cs_customer') == 0
    assert _count(database, 'cs_conversation_log') == 0
    assert _count(database, 'cs_outbox') == 0


def test_overlapping_failed_turn_does_not_undo_success(h5, monkeypatch):
    from catalog import llm
    from catalog.cs_chat import H5Bot

    app, database, _ = h5
    started, release = threading.Event(), threading.Event()
    original = H5Bot._text_turn

    def delayed_model(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return '{"actions":[]}'

    def fail_only_bad_replay(self, cust, text):
        if text == 'FAIL':
            if self.conn.execute('PRAGMA database_list').fetchone()[2]:
                self._log(cust['id'], 'user', text)
                raise RuntimeError('failed after writing')
            return 'planned'
        return original(self, cust, text)

    monkeypatch.setattr(llm, 'chat_text', delayed_model)
    monkeypatch.setattr(H5Bot, '_text_turn', fail_only_bad_replay)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                    base_url='http://test') as client:
            good = asyncio.create_task(client.post(
                '/cs/chat/test-shop/message',
                json={'visitor': app.state.test_guests['good'], 'text': '请记录一个黑色型号ABC'}))
            assert await asyncio.to_thread(started.wait, 2)
            bad = await asyncio.wait_for(client.post(
                '/cs/chat/test-shop/message',
                json={'visitor': app.state.test_guests['bad'], 'text': 'FAIL'}), 1.5)
            assert bad.status_code == 500
            release.set()
            assert (await good).status_code == 200

    asyncio.run(exercise())
    with sqlite3.connect(database) as connection:
        visitors = {row[0] for row in connection.execute('SELECT tg_id FROM cs_customer')}
        from catalog.guest_sessions import digest
        owner = lambda label: connection.execute('SELECT owner_id FROM guest_sessions WHERE token_hash=?',(digest(app.state.test_guests[label]),)).fetchone()[0]
        assert owner('good') in visitors
        assert owner('bad') not in visitors
        assert connection.execute('SELECT COUNT(*) FROM cs_conversation_log').fetchone()[0] >= 1


def test_text_turn_returns_conflict_after_bounded_replans(h5, monkeypatch):
    from catalog.cs_chat import H5Bot

    app, database, _ = h5
    plans = []

    def keep_changing_database(self, cust, text):
        assert not self.conn.execute('PRAGMA database_list').fetchone()[2]
        plans.append(1)
        with db.connect(str(database)) as writer:
            writer.execute('UPDATE shop_profile SET shop_name=? WHERE id=1',
                           (f'changed-{len(plans)}',))
        return 'planned'

    monkeypatch.setattr(H5Bot, '_text_turn', keep_changing_database)
    with TestClient(app) as client:
        response = client.post('/cs/chat/test-shop/message',
                               json={'visitor': app.state.test_guests['conflict'], 'text': 'hello'})
    assert response.status_code == 409
    assert len(plans) == 3
    assert _count(database, 'cs_customer') == 0


def test_failed_language_change_rolls_back_visitor(h5, monkeypatch):
    from catalog import cs_chat

    app, database, _ = h5
    original = cs_chat.set_language

    def fail_after_update(conn, cust, lang):
        original(conn, cust, lang)
        raise RuntimeError('failed after language update')

    monkeypatch.setattr(cs_chat, 'set_language', fail_after_update)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/cs/chat/test-shop/lang',
                               json={'visitor': app.state.test_guests['bad-language'], 'lang': 'English'})
    assert response.status_code == 500
    assert _count(database, 'cs_customer') == 0


def test_text_turn_reports_retriable_conflict_when_writer_is_busy(h5):
    app, database, _ = h5
    writer = sqlite3.connect(database)
    writer.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post('/cs/chat/test-shop/message',
                                   json={'visitor': app.state.test_guests['busy'], 'text': '找老板'})
        elapsed = time.monotonic() - started
    finally:
        writer.rollback()
        writer.close()
    assert response.status_code == 409
    assert elapsed < 3
    assert _count(database, 'cs_customer') == 0
