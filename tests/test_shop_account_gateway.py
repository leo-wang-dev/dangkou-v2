"""The public merchant gateway forwards only scoped buyer account requests."""
import httpx

from catalog import guest_sessions
from tests.test_tenant_chat import tenant_chat, issue


def test_gateway_claim_and_history_are_scoped_to_selected_tenant(tenant_chat, monkeypatch):
    t = tenant_chat
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL', 'http://127.0.0.1:19211')

    def central(url, *, headers, timeout, trust_env):
        token = headers['Authorization'].removeprefix('Bearer ')
        return httpx.Response(200, json={'kind': 'user', 'account_id': ('a' if token == 'one' else 'b') * 32})

    monkeypatch.setattr(httpx, 'get', central)
    _, chat_a, guest_a = issue(t, 0)
    _, chat_b, _ = issue(t, 1)
    owner = t.tenants[0].state.conn.execute('SELECT owner_id FROM guest_sessions WHERE token_hash=?',
                                            (guest_sessions.digest(guest_a),)).fetchone()[0]
    t.tenants[0].state.conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer-a',?)", (owner,))
    t.tenants[0].state.conn.execute("INSERT INTO cs_note(customer_id,fields_json) VALUES('buyer-a','{\"型号或品名\":\"A-only\"}')")
    t.tenants[0].state.conn.commit()

    auth = {'Authorization': 'Bearer one', 'X-Buyer-Email': 'forged@example.com', 'X-Buyer-Account-Id': 'b' * 32}
    claimed = t.client.post(chat_a + '/session/claim', headers=auth, json={'visitor': guest_a, 'email': 'forged@example.com'})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()['customer_id'] == 'buyer-a'
    a = t.client.get(chat_a + '/session', headers=auth)
    assert a.status_code == 200 and a.json()['notes'][0]['fields']['型号或品名'] == 'A-only'
    history = t.client.get(chat_a + '/history', headers=auth)
    assert history.status_code == 200 and history.json()['messages'] == []
    b = t.client.get(chat_b + '/session', headers=auth)
    assert b.status_code == 200 and b.json()['notes'] == []
    assert any(call[2].startswith('/' + chat_a.split('/merchant/customer/')[1].split('/', 1)[1]) and 'authorization' in call[3]
               for call in t.calls)
    assert all('x-buyer-email' not in call[3] and 'x-buyer-account-id' not in call[3] for call in t.calls)
    wrong_chat = '/merchant/customer/' + t.mids[1] + chat_a.split(t.mids[0], 1)[1]
    assert t.client.get(wrong_chat + '/session', headers=auth).status_code == 404
    assert t.client.get('/merchant/customer/' + t.mids[0] + '/admin', headers=auth).status_code == 404


def test_gateway_rejects_unlisted_methods_and_large_claim(tenant_chat):
    t = tenant_chat
    _, chat, _ = issue(t, 0)
    assert t.client.get(chat + '/session/claim').status_code == 404
    assert t.client.post(chat + '/history').status_code == 404
    huge = {'visitor': 'x' * 21000}
    assert t.client.post(chat + '/session/claim', json=huge).status_code == 413
