"""Slow vision calls cannot stall the HTTP event loop or mix request transactions."""
import asyncio
import json
import sqlite3
import threading

import httpx

from catalog import userapp
from tests.test_h5_transactions import h5


def test_h5_health_responds_while_vision_is_blocked(h5, monkeypatch):
    from catalog import llm

    app, _, photo = h5
    started, release = threading.Event(), threading.Event()

    def blocked_vision(*args, **kwargs):
        started.set()
        assert release.wait(5), 'test vision call was not released'
        return json.dumps([{'型号或品名': 'SAMPLE-1'}], ensure_ascii=False)

    monkeypatch.setattr(llm, 'chat_vision', blocked_vision)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='http://test') as client:
            photo_task = asyncio.create_task(client.post(
                '/cs/chat/test-shop/photo', data={'visitor': 'slow'},
                files={'file': ('p.jpg', photo, 'image/jpeg')}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                health = await asyncio.wait_for(client.get('/health'), 0.5)
                assert health.status_code == 200
                assert health.json()['status'] == 'ready'
                other = await asyncio.wait_for(client.post(
                    '/cs/chat/test-shop/lang',
                    json={'visitor': 'other', 'lang': 'English'}), 0.5)
                assert other.status_code == 200
            finally:
                release.set()
                assert (await photo_task).status_code == 200

    asyncio.run(exercise())


def test_userapp_guest_responds_while_vision_is_blocked(tmp_path):
    started, release = threading.Event(), threading.Event()

    class BlockedVision:
        def chat_vision(self, *args, **kwargs):
            started.set()
            assert release.wait(5), 'test vision call was not released'
            return json.dumps([{'型号或品名': 'SAMPLE-1'}], ensure_ascii=False)

    app = userapp.build_app(db_path=str(tmp_path / 'user.db'),
                            photo_dir=str(tmp_path / 'photos'),
                            codes_log=str(tmp_path / 'codes.log'), llm=BlockedVision())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='http://test') as client:
            photo_task = asyncio.create_task(client.post(
                '/photo', data={'owner': 'guest-abcdef'},
                files={'file': ('p.jpg', b'photo', 'image/jpeg')}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                guest = await asyncio.wait_for(client.post('/guest'), 0.5)
                assert guest.status_code == 200
                assert guest.json()['guest'].startswith('guest-')
            finally:
                release.set()
                assert (await photo_task).status_code == 200

    asyncio.run(exercise())


def test_h5_text_model_wait_allows_other_visitor_write_and_read(h5, monkeypatch):
    from catalog import llm

    app, database, _ = h5
    started, release = threading.Event(), threading.Event()
    calls = []

    conn = app.state.conn
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('patch-owner','h5-patch-owner')")
    conn.execute("INSERT INTO cs_link(token,customer_id) VALUES('patch-link','patch-owner')")
    note_id = conn.execute(
        "INSERT INTO cs_note(customer_id,fields_json,status) "
        "VALUES('patch-owner','{\"颜色\":\"红色\"}','draft')").lastrowid
    conn.commit()

    def blocked_text(*args, **kwargs):
        calls.append((args[0], json.dumps(args[1], ensure_ascii=False)))
        started.set()
        assert release.wait(6), 'test text call was not released'
        return '{"actions":[]}'

    monkeypatch.setattr(llm, 'chat_text', blocked_text)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                    base_url='http://test') as client:
            slow = asyncio.create_task(client.post(
                '/cs/chat/test-shop/message',
                json={'visitor': 'slow', 'text': '请记录一个黑色型号ABC'}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                other = await asyncio.wait_for(client.post(
                    '/cs/chat/test-shop/lang',
                    json={'visitor': 'other', 'lang': 'English'}), 1.5)
                assert other.status_code == 200
                edit = await asyncio.wait_for(client.patch(
                    f'/cs/link/patch-link/note/{note_id}',
                    json={'field': '颜色', 'value': '黑色'}), 1.5)
                assert edit.status_code == 200
                health = await asyncio.wait_for(client.get('/health'), 1.5)
                assert health.status_code == 200
            finally:
                release.set()
                assert (await slow).status_code == 200

    asyncio.run(exercise())
    assert len(calls) == len(set(calls))  # replan reuses identical paid calls
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM cs_conversation_log').fetchone()[0] >= 1
        assert json.loads(connection.execute('SELECT fields_json FROM cs_note WHERE id=?',
                                             (note_id,)).fetchone()[0])['颜色'] == '黑色'


def test_h5_text_replans_when_same_visitor_language_changes(h5, monkeypatch):
    from catalog import llm

    app, database, _ = h5
    started, release = threading.Event(), threading.Event()
    prompts = []

    def text_model(system, *args, **kwargs):
        prompts.append(system)
        if len(prompts) == 1:
            started.set()
            assert release.wait(5)
        return '{"actions":[]}'

    monkeypatch.setattr(llm, 'chat_text', text_model)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='http://test') as client:
            slow = asyncio.create_task(client.post(
                '/cs/chat/test-shop/message',
                json={'visitor': 'same', 'text': '请记录一个黑色型号ABC'}))
            assert await asyncio.to_thread(started.wait, 2)
            change = await asyncio.wait_for(client.post(
                '/cs/chat/test-shop/lang',
                json={'visitor': 'same', 'lang': 'English'}), 1.5)
            assert change.status_code == 200
            release.set()
            assert (await slow).status_code == 200

    asyncio.run(exercise())
    assert any('English' in prompt for prompt in prompts)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT lang FROM cs_customer WHERE tg_id='h5-same'").fetchone()[0] == 'English'


def test_h5_remote_catalog_wait_allows_other_write_and_reuses_result(h5, monkeypatch):
    from catalog import customer_catalog, shop_link

    app, _, _ = h5
    shop_id = shop_link.profile(app.state.conn)['shop_id']
    started, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setenv('CATALOG_CS_API_URL', 'https://catalog.invalid')
    monkeypatch.setenv('CATALOG_CS_SERVICE_TOKEN', 'test-token')

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {'shop_id': shop_id, 'products': []}

    class Session:
        trust_env = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def request(self, method, url, **kwargs):
            calls.append((method, url))
            started.set()
            assert release.wait(5)
            return Response()

    monkeypatch.setattr(customer_catalog.requests, 'Session', Session)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='http://test') as client:
            slow = asyncio.create_task(client.post(
                '/cs/chat/test-shop/message',
                json={'visitor': 'slow', 'text': '查询商品'}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                other = await asyncio.wait_for(client.post(
                    '/cs/chat/test-shop/lang',
                    json={'visitor': 'other', 'lang': 'English'}), 1.5)
                assert other.status_code == 200
            finally:
                release.set()
                assert (await slow).status_code == 200

    asyncio.run(exercise())
    assert len(calls) == 1


def test_h5_photo_cancel_waits_for_worker_and_rolls_back(h5, monkeypatch):
    from catalog import llm
    from catalog.cs_chat import H5Bot

    app, database, photo = h5
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []
    original = H5Bot._prepare_photo

    def blocked_vision(*args, **kwargs):
        started.set()
        assert release.wait(5), 'test vision call was not released'
        return json.dumps([{'型号或品名': 'SAMPLE-1'}], ensure_ascii=False)

    def observed_prepare(self, cust, data):
        try:
            return original(self, cust, data)
        except Exception as exc:
            errors.append(exc)
            raise
        finally:
            finished.set()

    monkeypatch.setattr(llm, 'chat_vision', blocked_vision)
    monkeypatch.setattr(H5Bot, '_prepare_photo', observed_prepare)

    class CapturedApp:
        scope = None

        async def __call__(self, scope, receive, send):
            self.scope = scope
            await app(scope, receive, send)

    captured = CapturedApp()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=captured, raise_app_exceptions=False),
                                    base_url='http://test') as client:
            task = asyncio.create_task(client.post(
                '/cs/chat/test-shop/photo', data={'visitor': 'cancelled'},
                files={'file': ('p.jpg', photo, 'image/jpeg')}))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not captured.scope['state']['database_worker_done'].is_set()
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert await asyncio.to_thread(finished.wait, 2)
            assert captured.scope['state']['database_worker_done'].is_set()

    asyncio.run(exercise())
    assert not errors
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM cs_note').fetchone()[0] == 0


def test_userapp_photo_cancel_waits_for_worker_and_rolls_back(tmp_path, monkeypatch):
    started, release, reached_db = threading.Event(), threading.Event(), threading.Event()

    class BlockedVision:
        def chat_vision(self, *args, **kwargs):
            started.set()
            assert release.wait(5), 'test vision call was not released'
            return json.dumps([{'型号或品名': 'SAMPLE-1'}], ensure_ascii=False)

    original = userapp.normalize_fields

    def observed_normalize(fields):
        reached_db.set()
        return original(fields)

    monkeypatch.setattr(userapp, 'normalize_fields', observed_normalize)
    database = tmp_path / 'user.db'
    app = userapp.build_app(db_path=str(database), photo_dir=str(tmp_path / 'photos'),
                            codes_log=str(tmp_path / 'codes.log'), llm=BlockedVision())

    class CapturedApp:
        scope = None

        async def __call__(self, scope, receive, send):
            self.scope = scope
            await app(scope, receive, send)

    captured = CapturedApp()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=captured, raise_app_exceptions=False),
                                    base_url='http://test') as client:
            task = asyncio.create_task(client.post(
                '/photo', data={'owner': 'guest-abcdef'},
                files={'file': ('p.jpg', b'photo', 'image/jpeg')}))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not captured.scope['state']['database_worker_done'].is_set()
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert await asyncio.to_thread(reached_db.wait, 2)
            assert captured.scope['state']['database_worker_done'].is_set()

    asyncio.run(exercise())
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM notes').fetchone()[0] == 0
