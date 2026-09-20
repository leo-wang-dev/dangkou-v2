import traceback

import pytest
import requests

from catalog.tg import TgApi, TgError


def test_explicit_proxy_covers_api_and_file_requests(monkeypatch):
    monkeypatch.setenv('TG_PROXY_URL', 'socks5h://127.0.0.1:17891')
    api = TgApi('test-secret')
    assert api.s.proxies == {
        'https': 'socks5h://127.0.0.1:17891',
        'http': 'socks5h://127.0.0.1:17891',
    }
    assert api.s.trust_env is False


@pytest.mark.parametrize('operation', ['api', 'photo'])
def test_network_failure_does_not_expose_bot_token(monkeypatch, operation):
    api = TgApi('test-private-token')
    monkeypatch.setattr('catalog.tg.time.sleep', lambda _: None)

    def fail(*args, **kwargs):
        raise requests.ConnectionError('failed URL /bottest-private-token/file')

    if operation == 'api':
        monkeypatch.setattr(api.s, 'post', fail)
        action = lambda: api._call('getMe')
    else:
        monkeypatch.setattr(api, '_call', lambda *a: {'file_path': 'photo.jpg'})
        monkeypatch.setattr(api.s, 'get', fail)
        action = lambda: api.download_photo([{'file_id': 'x', 'width': 10}])
    with pytest.raises(TgError) as error:
        action()
    assert 'test-private-token' not in ''.join(traceback.format_exception(error.value))
