"""WeChat mini-program identity joins the existing central notes account."""
from fastapi.testclient import TestClient
from fastapi import HTTPException

from catalog import userapp, wechat_auth
from tests.test_userapp import FakeLlm, _new_guest, _upload, _last_code


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv('USER_APP_ENV', 'development')
    monkeypatch.setenv('USER_APP_DEV_EMAIL_LOG', '1')
    monkeypatch.setenv('USER_APP_OTP_COOLDOWN_SECONDS', '0')
    identities = {'first': 'openid-one', 'again': 'openid-one',
                  'other': 'openid-two'}
    app = userapp.build_app(
        db_path=str(tmp_path / 'central.db'), photo_dir=str(tmp_path / 'photos'),
        codes_log=str(tmp_path / 'codes.log'), llm=FakeLlm(),
        wechat_exchange=lambda code: ('wx-test-app', identities[code]))
    return TestClient(app)


def _email_login(client, email, *, link_token=''):
    assert client.post('/auth/code', json={'email': email}).status_code == 200
    body = {'email': email, 'code': _last_code(client, email)}
    if link_token:
        body['link_token'] = link_token
    result = client.post('/auth/verify', json=body)
    assert result.status_code == 200, result.text
    return result.json()['token']


def test_wechat_login_merges_guest_and_recovers_notes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    guest = _new_guest(client)
    assert _upload(client, guest).json()['added'] == 1
    first = client.post('/auth/wechat', json={'code': 'first', 'guest': guest})
    assert first.status_code == 200, first.text
    assert first.json()['login_method'] == 'wechat'
    assert first.json()['email'] == ''
    token = first.json()['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.post('/notes', headers=headers).json()['notes'][0]['fields']['型号或品名'] == '直发夹板'
    assert client.post('/notes', params={'guest': guest}).status_code in (401, 410)
    assert client.get('/me', headers=headers).json()['login_method'] == 'wechat'
    second = client.post('/auth/wechat', json={'code': 'again'})
    assert second.status_code == 200
    assert second.json()['token'] != token
    assert len(client.post('/notes', headers={'Authorization': 'Bearer ' + second.json()['token']}).json()['notes']) == 1


def test_email_account_explicitly_binds_wechat(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    email = 'buyer@example.com'
    bearer = _email_login(client, email)
    guest = _new_guest(client)
    assert _upload(client, guest).status_code == 200
    linked = client.post('/auth/wechat', json={'code': 'first', 'guest': guest},
                         headers={'Authorization': 'Bearer ' + bearer})
    assert linked.status_code == 200, linked.text
    assert linked.json()['email'] == email
    assert linked.json()['login_method'] == 'email'
    assert len(client.post('/notes', headers={'Authorization': 'Bearer ' + bearer}).json()['notes']) == 1
    again = client.post('/auth/wechat', json={'code': 'again'})
    assert again.json()['email'] == email


def test_wechat_identity_cannot_be_bound_to_another_email(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first = _email_login(client, 'first@example.com')
    second = _email_login(client, 'second@example.com')
    assert client.post('/auth/wechat', json={'code': 'first'},
                       headers={'Authorization': 'Bearer ' + first}).status_code == 200
    denied = client.post('/auth/wechat', json={'code': 'again'},
                         headers={'Authorization': 'Bearer ' + second})
    assert denied.status_code == 409
    assert client.get('/me', headers={'Authorization': 'Bearer ' + second}).json()['email'] == 'second@example.com'


def test_wechat_account_can_link_existing_email_after_otp(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    email = 'saved@example.com'
    original = _email_login(client, email)
    guest = _new_guest(client)
    assert _upload(client, guest).status_code == 200
    wx = client.post('/auth/wechat', json={'code': 'first', 'guest': guest}).json()['token']
    linked = _email_login(client, email, link_token=wx)
    assert client.get('/me', headers={'Authorization': 'Bearer ' + linked}).json()['email'] == email
    assert len(client.post('/notes', headers={'Authorization': 'Bearer ' + original}).json()['notes']) == 1
    assert client.post('/auth/wechat', json={'code': 'again'}).json()['email'] == email


def test_reserved_wechat_email_cannot_request_otp(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post('/auth/code', json={'email': 'wx_abc@wechat.invalid'})
    assert response.status_code == 400


def test_wechat_code_exchange_keeps_secret_and_session_key_on_server(monkeypatch):
    monkeypatch.setenv('WECHAT_MINIAPP_APP_ID', 'wx-app')
    monkeypatch.setenv('WECHAT_MINIAPP_APP_SECRET', 'server-secret')
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'openid': 'wx-user', 'session_key': 'never-to-client'}
    monkeypatch.setattr(wechat_auth.requests, 'get',
                        lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    assert wechat_auth.exchange_code('single-use') == ('wx-app', 'wx-user')
    assert calls[0][0].endswith('/jscode2session')
    assert calls[0][1]['params']['js_code'] == 'single-use'
    assert calls[0][1]['params']['secret'] == 'server-secret'
    assert calls[0][1]['timeout'] == 8


def test_wechat_exchange_rejects_missing_configuration_and_invalid_code(monkeypatch):
    monkeypatch.delenv('WECHAT_MINIAPP_APP_ID', raising=False)
    monkeypatch.delenv('WECHAT_MINIAPP_APP_SECRET', raising=False)
    try:
        wechat_auth.exchange_code('bad')
        assert False
    except HTTPException as exc:
        assert exc.status_code == 503
    monkeypatch.setenv('WECHAT_MINIAPP_APP_ID', 'wx-app')
    monkeypatch.setenv('WECHAT_MINIAPP_APP_SECRET', 'server-secret')
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'errcode': 40029, 'errmsg': 'secret-value'}
    monkeypatch.setattr(wechat_auth.requests, 'get', lambda *args, **kwargs: Response())
    try:
        wechat_auth.exchange_code('bad')
        assert False
    except HTTPException as exc:
        assert exc.status_code == 401
        assert 'secret-value' not in exc.detail
