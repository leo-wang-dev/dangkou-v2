import asyncio
import contextvars
import io
import json
import sqlite3
import subprocess
import threading
import time

import anyio
import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from catalog import db, guest_sessions, llm, merchant_policy, userapp
from catalog.api import register_routes
from catalog.storage import LocalStorage


@pytest.mark.parametrize('surface', ['h5', 'central'])
@pytest.mark.parametrize('queued', [False, True])
def test_direct_endpoint_cancel(surface, queued, tmp_path, monkeypatch):
    # Enforce no child provider/subprocess even if a future code path changes.
    def forbidden(*a, **kw):
        raise AssertionError('offline probe forbids child provider/process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(llm, 'chat_text', lambda *a, **kw: '{"actions":[]}')
    started, release, thread_done = threading.Event(), threading.Event(), threading.Event()
    observations = {}
    timeline = []
    t0 = time.monotonic()
    def event(label):
        timeline.append((round(time.monotonic() - t0, 4), label))

    real_connect = sqlite3.connect
    class ObservedConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            if self is observations.get('connection'):
                event('middleware closes actual request connection')
            return super().close()
    def connect(*a, **kw):
        kw['factory'] = ObservedConnection
        return real_connect(*a, **kw)
    monkeypatch.setattr(sqlite3, 'connect', connect)

    def vision(*a, **kw):
        event('fake model entered in actual worker thread')
        started.set()
        assert release.wait(5), 'finite model hold expired'
        event('fake model released')
        return json.dumps([{'型号或品名': 'SAMPLE-1'}], ensure_ascii=False)
    monkeypatch.setattr(llm, 'chat_vision', vision)
    database = tmp_path / (surface + '.db')
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(picture, 'JPEG')
    if surface == 'h5':
        monkeypatch.setenv('CATALOG_CS_PHOTOS', str(tmp_path / 'photos'))
        monkeypatch.delenv('CATALOG_CS_API_URL', raising=False)
        conn = db.connect(str(database))
        db.init_db(conn)
        merchant_policy.apply(conn, {'wechat_managed': True}, 1)
        conn.execute("UPDATE shop_profile SET chat_token='test-shop', owner_wechat='boss'")
        capability = guest_sessions.issue(conn)
        conn.commit()
        app = FastAPI()
        app.state.conn = conn
        app.state.token = 'test-token'
        app.state.storage = LocalStorage(str(tmp_path / 'images'))
        app.state.callback = None
        register_routes(app)
        route, table = '/cs/chat/test-shop/photo', 'cs_note'
    else:
        class FakeLLM:
            chat_vision = staticmethod(vision)
        app = userapp.build_app(db_path=str(database), photo_dir=str(tmp_path / 'photos'),
                                codes_log=str(tmp_path / 'codes.log'), llm=FakeLLM())
        route, table = '/photo', 'notes'

    # Capture the actual registered async endpoint, not a new helper runner.
    endpoint_route = next(r for r in app.routes if getattr(r, 'path', None) == ('/cs/chat/{token}/photo' if surface == 'h5' else route))
    original_endpoint = endpoint_route.dependant.call
    async def capture_endpoint(**kwargs):
        observations['endpoint_task'] = asyncio.current_task()
        return await original_endpoint(**kwargs)
    endpoint_route.dependant.call = capture_endpoint

    real_run_sync = anyio.to_thread.run_sync
    async def observed_run_sync(work, *a, **kw):
        if kw.get('limiter') is None:
            return await real_run_sync(work, *a, **kw)
        observations['runner_task'] = asyncio.current_task()
        observations['limiter'] = kw['limiter']
        observations['connection'] = next(v for k, v in contextvars.copy_context().items()
            if k.name in ('catalog_request_connection', 'userapp_request_connection'))
        if queued:
            kw['limiter'].total_tokens = 1
            observations['holder'] = object()
            kw['limiter'].acquire_on_behalf_of_nowait(observations['holder'])
            started.set()
        def actual_work(*args):
            try:
                return work(*args)
            except BaseException as exc:
                observations['thread_error'] = type(exc).__name__ + ': ' + str(exc)
                event('actual worker error: ' + observations['thread_error'])
                raise
            finally:
                event('actual worker thread callback exits')
                thread_done.set()
        return await real_run_sync(actual_work, *a, **kw)
    monkeypatch.setattr(anyio.to_thread, 'run_sync', observed_run_sync)
    class Capture:
        async def __call__(self, scope, receive, send):
            if scope['path'] == route:
                observations['scope'] = scope
            await app(scope, receive, send)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Capture(), raise_app_exceptions=False),
                                     base_url='http://test') as client:
            if surface == 'h5':
                response = await client.post('/cs/chat/test-shop/mode', json={'visitor': capability, 'mode':'notes'})
                assert response.status_code == 200
                data = {'visitor': capability}
            else:
                response = await client.post('/guest')
                assert response.status_code == 200
                data = {'owner': response.json()['guest']}
            outer = asyncio.create_task(client.post(route, data=data,
                files={'file': ('p.jpg', picture.getvalue(), 'image/jpeg')}))
            try:
                assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 2.5)
                endpoint = observations['endpoint_task']
                assert endpoint is not outer
                limiter = observations['limiter']
                assert limiter.borrowed_tokens == 1
                assert not observations['connection'].closed
                assert not observations['scope']['state']['database_worker_done'].is_set()
                event('raw asyncio cancel actual downstream endpoint task; outer task untouched')
                endpoint.cancel()
                if queued:
                    await asyncio.wait_for(asyncio.shield(outer), 1)
                    assert not thread_done.is_set()
                    assert observations['scope']['state']['database_worker_done'].is_set()
                    assert observations['connection'].closed
                    assert limiter.borrowed_tokens == 1  # only the unrelated holder
                    return
                await asyncio.sleep(.05)
                endpoint.cancel()
                await asyncio.sleep(.05)
                assert not outer.done()
                assert not observations['scope']['state']['database_worker_done'].is_set()
                assert not observations['connection'].closed
                assert limiter.borrowed_tokens == 1
                # Occupy the other three slots; replacement work must stay blocked.
                holders = [object() for _ in range(3)]
                for holder in holders:
                    limiter.acquire_on_behalf_of_nowait(holder)
                try:
                    with pytest.raises(anyio.WouldBlock):
                        limiter.acquire_on_behalf_of_nowait(object())
                finally:
                    for holder in holders:
                        limiter.release_on_behalf_of(holder)
            finally:
                release.set()
                if queued:
                    observations['limiter'].release_on_behalf_of(observations['holder'])
                else:
                    assert await asyncio.wait_for(asyncio.to_thread(thread_done.wait, 2), 2.5)
                if not outer.done():
                    await asyncio.wait_for(outer, 1)
    asyncio.run(exercise())
    with real_connect(database) as check:
        count = check.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
    app.state.conn.close()
    assert observations.get('thread_error') is None
    assert count == 0
    assert observations['limiter'].borrowed_tokens == 0
    assert observations['connection'].closed
