import json
from pathlib import Path

import pytest


class Response:
    def __init__(self, status, value):
        self.status_code = status
        self._value = value
        self.is_success = 200 <= status < 300

    def json(self):
        return self._value


class FakeClient:
    state = {'session': {'state': 'idle', 'sessionId': None}, 'deleted': []}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def request(self, method, path, json=None):
        if path == '/v1/accounts' and method == 'GET':
            account = {'normalizedAccountId': 'old-account', 'userId': 'old-owner'}
            if self.state['session'].get('state') in ('confirmed', 'finalized'):
                account = {'normalizedAccountId': 'new-account', 'userId': 'new-owner'}
            return Response(200, {'ok': True, 'accounts': [account]})
        if path == '/v1/login/session':
            return Response(200, {'ok': True, 'session': self.state['session']})
        if path == '/v1/login/start':
            self.state['session'] = {'state': 'qr_ready', 'sessionId': 'session-new',
                                      'qrPngBase64': 'abc', 'expiresAt': '2099-01-01T00:00:00Z'}
            return Response(200, {'ok': True, 'session': self.state['session']})
        if path == '/v1/login/finalize':
            self.state['session'] = {'state': 'confirmed', 'sessionId': 'session-new',
                                      'normalizedAccountId': 'new-account'}
            return Response(200, {'ok': True, 'account': {'normalizedAccountId': 'new-account', 'userId': 'new-owner', 'accountId': 'new-account'}})
        if method == 'DELETE':
            self.state['deleted'].append(path)
            return Response(200, {'ok': True})
        raise AssertionError((method, path, json))


@pytest.fixture
def service(tmp_path, monkeypatch):
    from catalog import wechat_binding
    binding = tmp_path / 'wechat-binding.json'
    token = tmp_path / 'admin-token'
    token.write_text('fixture-token')
    monkeypatch.setenv('WECHAT_BINDING_FILE', str(binding))
    monkeypatch.setenv('WECHAT_BINDING_ADMIN_URL', 'http://127.0.0.1:17605')
    monkeypatch.setenv('WECHAT_BINDING_ADMIN_TOKEN_FILE', str(token))
    monkeypatch.setattr(wechat_binding.httpx, 'Client', FakeClient)
    FakeClient.state = {'session': {'state': 'idle', 'sessionId': None}, 'deleted': []}
    return wechat_binding.BindingService(), binding


def test_start_shows_qr_and_finalize_replaces_old_account(service):
    svc, binding = service
    with svc.locked():
        first = svc.start()
        assert first['session']['state'] == 'qr_ready'
        assert first['session']['qrPngBase64'] == 'abc'
        svc.call('GET', '/v1/login/session')  # fake remains deterministic
        FakeClient.state['session'] = {'state': 'confirmed', 'sessionId': 'session-new',
                                       'normalizedAccountId': 'new-account'}
        result = svc.finalize('session-new')
    assert result == {'bound': True, 'replaced': True}
    assert json.loads(binding.read_text())['userId'] == 'new-owner'
    assert FakeClient.state['deleted'] == ['/v1/accounts/old-account']


def test_page_has_refresh_and_replacement_copy():
    page = Path(__file__).parents[1] / 'static' / 'merchant' / 'wechat-bind.html'
    text = page.read_text()
    assert 'setTimeout(poll,2000)' in text
    assert '替换原来绑定的微信' in text
    assert 'qrPngBase64' in text and 'wechat-binding/finalize' in text
    index = (page.parents[1] / 'index.html').read_text()
    assert 'id="v-wechat"' in index and 'id="wechat-frame"' in index


def test_external_qr_url_is_converted_to_inline_image(service, monkeypatch):
    from catalog import wechat_binding
    svc, _ = service
    FakeClient.state['session'] = {'state': 'qr_ready', 'sessionId': 'session-new',
                                   'qrUrl': 'https://liteapp.weixin.qq.com/qr'}
    class Image:
        is_success = True
        content = b'png-bytes'
        headers = {'content-type': 'image/png'}
    monkeypatch.setattr(wechat_binding.httpx, 'get', lambda *a, **k: Image())
    with svc.locked():
        svc.operation.write_text(json.dumps({'sessionId': 'session-new'}))
        result = svc.status()
    # The provider sometimes labels an HTML/invalid payload as image/png.
    # The service must turn the QR URL into a real QR PNG instead of returning
    # bytes that the browser cannot decode.
    import base64
    assert base64.b64decode(result['session']['qrPngBase64']).startswith(b'\x89PNG\r\n\x1a\n')
