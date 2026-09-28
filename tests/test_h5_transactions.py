"""File-backed H5 requests must expose only complete, durable business turns."""
import io
import json
import sqlite3

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
        response = client.post('/cs/chat/test-shop/photo', data={'visitor': 'one'},
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
                               json={'visitor': 'two', 'text': '找老板'})
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
                               json={'visitor': 'failure', 'text': 'hello'})
    assert response.status_code == 500
    assert _count(database, 'cs_customer') == 0
    assert _count(database, 'cs_conversation_log') == 0


def test_failed_turn_does_not_undo_another_visitors_success(h5, monkeypatch):
    from catalog.cs_chat import H5Bot

    app, database, _ = h5
    with TestClient(app) as client:
        good = client.post('/cs/chat/test-shop/message',
                           json={'visitor': 'good', 'text': '找老板'})
        assert good.status_code == 200

    def fail_after_log(self, cust, text):
        self._log(cust['id'], 'user', text)
        raise RuntimeError('failed after writing')

    monkeypatch.setattr(H5Bot, '_text_turn', fail_after_log)
    with TestClient(app, raise_server_exceptions=False) as client:
        bad = client.post('/cs/chat/test-shop/message',
                          json={'visitor': 'bad', 'text': 'hello'})
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
        response = client.post('/photo', data={'owner': 'guest-abcdef'},
                               files={'file': ('p.jpg', b'photo', 'image/jpeg')})
        later = client.post('/auth/code', json={'email': 'buyer@example.com'})
    assert response.status_code == 500
    assert later.status_code == 200
    assert _count(database, 'notes') == 0
