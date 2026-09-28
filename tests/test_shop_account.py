"""Authenticated procurement sessions keep shop history without trusting a client email."""
import json
import asyncio
import threading
import time

import httpx
from fastapi.testclient import TestClient

from catalog import guest_sessions
from tests.test_h5_transactions import h5


def _central(monkeypatch, account_by_token=None, unavailable=False):
    account_by_token = account_by_token or {'one': 'a' * 32, 'two': 'b' * 32}
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL', 'http://127.0.0.1:19211')

    def respond(url, *, headers, timeout, trust_env):
        assert url == 'http://127.0.0.1:19211/me'
        assert timeout <= 5 and trust_env is False
        if unavailable:
            raise httpx.ConnectError('central unavailable')
        token = headers.get('Authorization', '').removeprefix('Bearer ')
        if token not in account_by_token:
            return httpx.Response(401, json={'detail': 'invalid token'})
        return httpx.Response(200, json={'kind': 'user', 'account_id': account_by_token[token], 'email': 'ignored@example.com'})

    monkeypatch.setattr(httpx, 'get', respond)


def _seed_guest(app, client, name, *, with_card=False):
    visitor = client.post('/cs/chat/test-shop/session').json()['visitor']
    conn = app.state.conn
    owner = conn.execute('SELECT owner_id FROM guest_sessions WHERE token_hash=?',
                         (guest_sessions.digest(visitor),)).fetchone()[0]
    cid = f'customer-{name}'
    conn.execute('INSERT INTO cs_customer(id,tg_id) VALUES(?,?)', (cid, owner))
    batch = None
    if with_card:
        conn.execute("INSERT INTO note_batches(owner_kind,owner_id,state,fields_json,active) VALUES('guest',?,'confirmed',?,1)",
                     (cid, json.dumps({'档口名称': 'A档', '供应商联系方式': '123'})))
        batch = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute('INSERT INTO cs_note(customer_id,fields_json,status,batch_id) VALUES(?,?,?,?)',
                 (cid, json.dumps({'型号或品名': name, '价格': '12'}), 'confirmed', batch))
    note_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute("INSERT INTO cs_conversation_log(customer_id,role,content) VALUES(?,'user',?)", (cid, f'查{name}'))
    old_link = f'old-{name}-link-123456789012'
    conn.execute('INSERT INTO cs_link(token,customer_id) VALUES(?,?)', (old_link, cid))
    conn.commit()
    return visitor, cid, note_id, batch, old_link


def test_claim_merges_guest_notes_card_history_and_reissues_link(h5, monkeypatch):
    app, _, _ = h5
    _central(monkeypatch)
    with TestClient(app) as client:
        visitor, cid, note_id, batch, old_link = _seed_guest(app, client, 'Item-1', with_card=True)
        auth = {'Authorization': 'Bearer one'}
        response = client.post('/cs/chat/test-shop/session/claim', json={'visitor': visitor}, headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()['customer_id'] == cid
        assert client.get('/cs/chat/test-shop/session', params={'visitor': visitor}).status_code == 410
        state = client.get('/cs/chat/test-shop/session', headers=auth)
        assert state.status_code == 200, state.text
        assert [n['id'] for n in state.json()['notes']] == [note_id]
        assert state.json()['batches'][0]['id'] == batch
        history = client.get('/cs/chat/test-shop/history', headers=auth).json()['messages']
        assert history[-1]['content'] == '查Item-1'
        assert client.get('/cs/link/' + old_link).status_code == 410
        new_link = client.get('/cs/chat/test-shop/list-token', headers=auth).json()['token']
        assert new_link and new_link != old_link
        assert client.get('/cs/link/' + new_link, headers=auth).status_code == 200
        again = client.post('/cs/chat/test-shop/session/claim', json={'visitor': visitor}, headers=auth)
        assert again.status_code == 200 and again.json()['customer_id'] == cid
        assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0] == 1


def test_existing_account_merge_is_idempotent_and_isolated(h5, monkeypatch):
    app, _, _ = h5
    _central(monkeypatch)
    with TestClient(app) as client:
        first, cid, note_id, batch, _ = _seed_guest(app, client, 'First', with_card=True)
        one = {'Authorization': 'Bearer one'}
        assert client.post('/cs/chat/test-shop/session/claim', json={'visitor': first}, headers=one).status_code == 200
        second, second_cid, second_note, second_batch, _ = _seed_guest(app, client, 'Second', with_card=True)
        assert client.post('/cs/chat/test-shop/session/claim', json={'visitor': second}, headers=one).status_code == 200
        assert [n['id'] for n in client.get('/cs/chat/test-shop/session', headers=one).json()['notes']] == [note_id, second_note]
        assert app.state.conn.execute('SELECT active FROM note_batches WHERE id=?', (batch,)).fetchone()[0] == 1
        assert app.state.conn.execute('SELECT active FROM note_batches WHERE id=?', (second_batch,)).fetchone()[0] == 0
        assert app.state.conn.execute('SELECT count(*) FROM cs_customer WHERE id=?', (second_cid,)).fetchone()[0] == 0
        assert client.post('/cs/chat/test-shop/session/claim', json={'visitor': second}, headers={'Authorization': 'Bearer two'}).status_code in (409, 410)
        other = client.get('/cs/chat/test-shop/session', headers={'Authorization': 'Bearer two'})
        assert other.status_code == 200 and other.json()['notes'] == []
        app.state.conn.execute('UPDATE guest_sessions SET expires_at=? WHERE state="active"', (time.time()-2,))
        app.state.conn.commit()
        client.post('/cs/chat/test-shop/session')
        assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0] == 2


def test_invalid_expired_and_unavailable_identity_never_claim_guest(h5, monkeypatch):
    app, _, _ = h5
    _central(monkeypatch)
    with TestClient(app) as client:
        visitor, cid, _, _, _ = _seed_guest(app, client, 'Safe')
        path = '/cs/chat/test-shop/session/claim'
        assert client.post(path, json={'visitor': visitor}).status_code == 401
        assert client.post(path, json={'visitor': visitor}, headers={'Authorization': 'Bearer invalid'}).status_code == 401
        _central(monkeypatch, unavailable=True)
        assert client.post(path, json={'visitor': visitor}, headers={'Authorization': 'Bearer one'}).status_code == 503
        assert client.get('/cs/chat/test-shop/session', params={'visitor': visitor}).status_code == 200
        _central(monkeypatch)
        app.state.conn.execute('UPDATE guest_sessions SET expires_at=0 WHERE token_hash=?', (guest_sessions.digest(visitor),))
        app.state.conn.commit()
        assert client.post(path, json={'visitor': visitor}, headers={'Authorization': 'Bearer one'}).status_code == 410
        assert app.state.conn.execute('SELECT count(*) FROM cs_customer WHERE id=? AND account_id!=""', (cid,)).fetchone()[0] == 0


def test_logged_in_photo_and_card_belong_to_persistent_owner(h5, monkeypatch):
    from catalog import llm
    app, _, photo = h5
    _central(monkeypatch)
    monkeypatch.setattr(llm, 'chat_vision', lambda *a, **kw: json.dumps([{'型号或品名': 'Logged-photo'}]))
    auth = {'Authorization': 'Bearer one'}
    with TestClient(app) as client:
        assert client.get('/cs/chat/test-shop/session', headers=auth).status_code == 200
        assert client.post('/cs/chat/test-shop/mode', json={'mode': 'notes'}, headers=auth).status_code == 200
        response = client.post('/cs/chat/test-shop/photo', data={}, files={'file': ('photo.jpg', photo)}, headers=auth)
        assert response.status_code == 200, response.text
        state = client.get('/cs/chat/test-shop/session', headers=auth).json()
        assert state['notes'][0]['fields']['型号或品名'] == 'Logged-photo'
        customer_id = app.state.conn.execute("SELECT id FROM cs_customer WHERE account_id=?", ('a' * 32,)).fetchone()[0]
        assert app.state.conn.execute("SELECT count(*) FROM note_batches WHERE owner_kind='user' AND owner_id=?", (customer_id,)).fetchone()[0] >= 1
        token = client.get('/cs/chat/test-shop/list-token', headers=auth).json()['token']
        assert client.get('/cs/link/' + token + '/export.xlsx', headers=auth).status_code == 200


def test_slow_identity_lookup_does_not_block_shop_health(h5, monkeypatch):
    app, _, photo = h5
    started, release = threading.Event(), threading.Event()
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL', 'http://127.0.0.1:19211')

    def blocked(url, *, headers, timeout, trust_env):
        started.set()
        assert release.wait(3)
        return httpx.Response(200, json={'kind': 'user', 'account_id': 'a' * 32})

    monkeypatch.setattr(httpx, 'get', blocked)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            timer = threading.Timer(1, release.set)
            timer.start()
            begin = time.monotonic()
            task = asyncio.create_task(client.post('/cs/chat/test-shop/photo', data={},
                                                   files={'file': ('p.jpg', photo)},
                                                   headers={'Authorization': 'Bearer slow'}))
            assert await asyncio.to_thread(started.wait, 2)
            try:
                result = await client.get('/health')
                assert result.status_code == 200
                assert time.monotonic() - begin < 0.5
            finally:
                release.set()
                timer.cancel()
                assert (await task).status_code == 200

    asyncio.run(exercise())


def test_claim_keeps_unprocessed_guest_photo_and_selected_mode(h5, monkeypatch, tmp_path):
    app, _, _ = h5
    _central(monkeypatch)
    pending = tmp_path / 'pending.jpg'
    pending.write_bytes(b'queued-image')
    with TestClient(app) as client:
        visitor = client.post('/cs/chat/test-shop/session').json()['visitor']
        app.state.conn.execute('UPDATE guest_sessions SET pending_photo=? WHERE token_hash=?',
                               (str(pending), guest_sessions.digest(visitor)))
        app.state.conn.commit()
        auth = {'Authorization': 'Bearer one'}
        response = client.post('/cs/chat/test-shop/session/claim', json={'visitor': visitor}, headers=auth)
        assert response.status_code == 200, response.text
        state = client.get('/cs/chat/test-shop/session', headers=auth).json()
        assert state['intent_required'] is True
        assert pending.exists()
        account = app.state.conn.execute("SELECT pending_photo FROM guest_sessions WHERE owner_id=?", ('h5-acct-' + 'a'*32,)).fetchone()
        assert account[0] == str(pending)
