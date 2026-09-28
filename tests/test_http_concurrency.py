"""Slow vision calls cannot stall the HTTP event loop or mix request transactions."""
import asyncio
import json
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
