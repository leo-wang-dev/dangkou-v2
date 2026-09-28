import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from catalog import api as catalog_api, db, dynamic_import
from catalog.main import app
from catalog.storage import LocalStorage


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(str(tmp_path / 'wait.sqlite'))
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = 'T0KEN'
    app.state.storage = LocalStorage(str(tmp_path))
    app.state.callback = None
    yield TestClient(app)
    from catalog import ingest
    assert ingest.join_workers(timeout=5)
    conn.close()


def _fake_payload(conn, xlsx_path, work_dir, **kw):
    return {'sheets': [{'template': {'name': 'Sheet1', 'fields': [
        {'key': 'model', 'label': '型号', 'visibility': 'public'}]}}]}


def test_template_wait_returns_inline_and_keeps_durable_push(client, tmp_path, monkeypatch):
    """wait 模式：模板解析结果随 HTTP 响应带回，完成事件仍持久化，HTTP 响应不能证明用户已收到。"""
    f = tmp_path / 'a.xlsx'
    f.write_bytes(b'PK\x03\x04fake')
    calls = []
    app.state.callback = lambda **kw: calls.append(kw)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', _fake_payload)
    r = client.post('/import', headers={'X-Service-Token': 'T0KEN'},
                    json={'path': str(f), 'phase': 'template', 'mode': 'new', 'wait': True})
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'ticketed'
    assert body['stats']['categories'] == ['Sheet1']
    time.sleep(0.5)
    assert len(calls) == 1
    assert app.state.conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_template_wait_timeout_falls_back_to_push(client, tmp_path, monkeypatch):
    """wait 超时：返回 est_sec 走异步路径，完成回调恢复出站推送（通知不丢）。"""
    f = tmp_path / 'a.xlsx'
    f.write_bytes(b'PK\x03\x04fake')
    calls = []

    def slow(conn, xlsx_path, work_dir, **kw):
        time.sleep(1.5)
        return _fake_payload(conn, xlsx_path, work_dir, **kw)

    app.state.callback = lambda **kw: calls.append(kw)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', slow)
    monkeypatch.setattr(catalog_api, 'TEMPLATE_WAIT_SEC', 0.3)
    r = client.post('/import', headers={'X-Service-Token': 'T0KEN'},
                    json={'path': str(f), 'phase': 'template', 'mode': 'new', 'wait': True})
    body = r.json()
    assert 'est_sec' in body and 'status' not in body
    for _ in range(100):
        if calls:
            break
        time.sleep(0.1)
    assert calls and calls[0]['stats']['phase'] == 'template'


def test_products_and_default_calls_keep_push(client, tmp_path, monkeypatch):
    """非 wait 调用（products / 旧调用方）行为不变：完成仍走出站推送。"""
    f = tmp_path / 'a.xlsx'
    f.write_bytes(b'PK\x03\x04fake')
    calls = []
    app.state.callback = lambda **kw: calls.append(kw)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', _fake_payload)
    r = client.post('/import', headers={'X-Service-Token': 'T0KEN'},
                    json={'path': str(f), 'phase': 'template', 'mode': 'new'})
    assert 'est_sec' in r.json()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.1)
    assert calls and calls[0]['stats']['phase'] == 'template'


def test_cancelled_wait_keeps_connection_until_worker_and_completion_durable(client, tmp_path, monkeypatch):
    import asyncio
    import threading
    import httpx
    from catalog import ingest
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return _fake_payload(*args, **kwargs)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', slow)
    source = tmp_path / 'cancel.xlsx'; source.write_bytes(b'fake')
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            task = asyncio.create_task(http.post('/import', headers={'X-Service-Token': 'T0KEN'},
                json={'path': str(source), 'phase': 'template', 'mode': 'new', 'wait': True}))
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel(); await asyncio.sleep(.03); task.cancel()
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(exercise())
    assert ingest.join_workers(timeout=5)
    assert app.state.conn.execute('SELECT status FROM import_doc').fetchone()[0] == 'ticketed'
    assert app.state.conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1
