import concurrent.futures
import io
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from catalog import userapp

@pytest.fixture
def auth(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(userapp, '_send_code_stub', lambda email, code, log: sent.append(code))
    app = userapp.build_app(str(tmp_path/'auth.db'), str(tmp_path/'photos'), str(tmp_path/'codes'))
    yield TestClient(app), sent
    app.state.conn.close()

def send(client):
    return client.post('/auth/code', json={'email':'buyer@example.com'})

def verify(client, code):
    return client.post('/auth/verify', json={'email':'buyer@example.com','code':code})

def test_resend_supersedes_and_consumes_once(auth, monkeypatch):
    client, sent = auth
    monkeypatch.setenv('USER_APP_OTP_COOLDOWN_SECONDS','0')
    assert send(client).status_code == 200
    old = sent[-1]
    assert send(client).status_code == 200
    assert verify(client, old).status_code == 400
    assert verify(client, sent[-1]).status_code == 200
    assert verify(client, sent[-1]).status_code == 400

def test_attempt_cap_persists_and_expiry(auth, monkeypatch):
    client, sent = auth
    monkeypatch.setenv('USER_APP_OTP_MAX_ATTEMPTS','2')
    assert send(client).status_code == 200
    assert verify(client,'wrong').status_code == 400
    assert verify(client,'wrong').status_code == 400
    assert verify(client,sent[-1]).status_code == 400

def test_cooldown_and_failed_delivery(auth, monkeypatch):
    client, sent = auth
    assert send(client).status_code == 200
    assert send(client).status_code == 429
    monkeypatch.setenv('USER_APP_OTP_COOLDOWN_SECONDS','0')
    def fail(email,code,log):
        sent.append(code)
        raise RuntimeError('provider failure')
    monkeypatch.setattr(userapp,'_send_code_stub',fail)
    assert send(client).status_code == 503
    assert verify(client,sent[-1]).status_code == 400
    assert verify(client,sent[0]).status_code == 400

def test_concurrent_send_no_writer_during_provider_and_single_verify(auth, monkeypatch):
    client, sent = auth
    entered=threading.Event(); release=threading.Event()
    def block(email,code,log):
        sent.append(code); entered.set(); assert release.wait(5)
    monkeypatch.setattr(userapp,'_send_code_stub',block)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        first=pool.submit(send,client)
        assert entered.wait(3)
        try:
            db=sqlite3.connect(client.app.state.conn.execute('PRAGMA database_list').fetchone()[2],timeout=.2)
            db.execute('BEGIN IMMEDIATE'); db.rollback(); db.close()
            assert send(client).status_code == 429
            assert verify(client,sent[-1]).status_code == 400
        finally: release.set()
        assert first.result().status_code == 200
        results=list(pool.map(lambda _:verify(client,sent[-1]).status_code,range(2)))
        assert sorted(results)==[200,400]

def test_production_missing_mail_never_logs_code(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('RESEND_API_KEY',raising=False)
    monkeypatch.delenv('RESEND_FROM',raising=False)
    monkeypatch.delenv('USER_APP_DEV_EMAIL_LOG',raising=False)
    with pytest.raises(RuntimeError):
        userapp._send_code_stub('buyer@example.com','123456',str(tmp_path/'codes'))
    assert '123456' not in capsys.readouterr().out
    assert not (tmp_path/'codes').exists()

def test_photo_bounds_before_model(auth, monkeypatch):
    client,_=auth
    guest=client.post('/guest').json()['guest']
    def photo(data):
        return client.post('/photo',data={'owner':guest},files={'file':('x.jpg',data,'image/jpeg')})
    assert photo(b'not an image').status_code == 400
    monkeypatch.setenv('CATALOG_UPLOAD_MAX_BYTES','10')
    assert photo(b'x'*11).status_code == 413
    monkeypatch.setenv('CATALOG_UPLOAD_MAX_BYTES','100000')
    monkeypatch.setenv('CATALOG_UPLOAD_MAX_PIXELS','10')
    buf=io.BytesIO(); Image.new('RGB',(4,4)).save(buf,'PNG')
    assert photo(buf.getvalue()).status_code == 400


def test_chunked_upload_total_body_is_bounded(auth,monkeypatch):
    client,_=auth
    monkeypatch.setenv('CATALOG_UPLOAD_MAX_BYTES','10')
    body=(b'--x\r\nContent-Disposition: form-data; name="file"; filename="x.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'+b'x'*70000+b'\r\n--x--\r\n')
    response=client.post('/photo',headers={'content-type':'multipart/form-data; boundary=x'},content=iter([body[:100],body[100:]]))
    assert response.status_code == 413
    assert not list(__import__('pathlib').Path(client.app.state.photo_dir).iterdir())


def test_localized_provider_mail_and_error(auth,monkeypatch):
    from catalog import cs_i18n
    import requests
    client,_=auth
    sender=__import__('importlib').reload(userapp)._send_code_stub
    monkeypatch.setenv('RESEND_API_KEY','test-only')
    monkeypatch.setenv('RESEND_FROM','trial@example.com')
    monkeypatch.setenv('USER_APP_OTP_TTL_SECONDS','123')
    calls=[]
    class Response: status_code=200
    monkeypatch.setattr(requests,'post',lambda *a,**kw:calls.append(kw) or Response())
    monkeypatch.setattr(userapp,'_send_code_stub',sender)
    for lang in cs_i18n.CODES:
        email=f'{lang}@example.com'
        result=client.post('/auth/code',headers={'X-Customer-Language':lang},json={'email':email})
        assert result.status_code==200
        assert calls[-1]['json']['subject']==cs_i18n.t('otpEmailSubject',lang)
        assert '123' in calls[-1]['json']['text']
        retry=client.post('/auth/code',headers={'X-Customer-Language':lang},json={'email':email})
        assert retry.status_code==429
        assert retry.json()['detail']==cs_i18n.t('otpCooldown',lang)
