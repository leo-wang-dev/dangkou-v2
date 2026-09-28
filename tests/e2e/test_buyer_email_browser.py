"""Click through guest notes, email login, claim, and cross-device recovery over HTTP."""
import socket
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

from catalog import userapp
from tests.test_h5_transactions import h5
from tests.test_tenant_chat import picture


def test_buyer_email_login_claim_and_second_device(h5, tmp_path, monkeypatch):
    app, _, _ = h5
    monkeypatch.setenv('USER_APP_ENV', 'development')
    monkeypatch.setenv('USER_APP_DEV_EMAIL_LOG', '1')
    central = userapp.build_app(db_path=str(tmp_path / 'userapp.db'),
                                photo_dir=str(tmp_path / 'central-photos'),
                                codes_log=str(tmp_path / 'codes.log'))
    app.mount('/tool', central)
    app.mount('/', StaticFiles(directory=str(Path(__file__).resolve().parents[2] / 'static'), html=True))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL', f'http://127.0.0.1:{port}/tool')
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]})
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        assert server.started
        base = f'http://127.0.0.1:{port}'
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            first = browser.new_context()
            page = first.new_page()
            page.goto(base + '/cs/chat/test-shop')
            page.locator('#file').set_input_files({'name': 'product.jpg', 'mimeType': 'image/jpeg', 'buffer': picture()})
            page.locator('.session-controls button', has_text='记笔记').first.click()
            expect(page.locator('#log')).to_contain_text('SAMPLE-1')
            guest = page.evaluate("sessionStorage.getItem('h5v:test-shop')")
            assert guest
            page.locator('#loginbtn').click()
            page.locator('#email').fill('buyer@example.com')
            page.locator('#sendbtn').click()
            expect(page.locator('#sendbtn')).to_be_enabled()
            code = (tmp_path / 'codes.log').read_text().splitlines()[-1].split('\t')[-1]
            page.locator('#code').fill(code)
            page.locator('#verifybtn').click()
            expect(page.locator('#who')).to_have_text('buyer@example.com')
            expect(page.locator('#log')).to_contain_text('SAMPLE-1')
            assert page.evaluate("sessionStorage.getItem('h5v:test-shop')") is None
            token = page.evaluate("localStorage.getItem('ut_token')")
            assert token
            state = page.request.get(base + '/cs/chat/test-shop/session', headers={'Authorization': 'Bearer ' + token})
            assert state.status == 200 and state.json()['notes'][0]['fields']['型号或品名'] == 'SAMPLE-1'
            second = browser.new_context()
            second.add_init_script(f"localStorage.setItem('ut_token',{token!r});localStorage.setItem('ut_email','buyer@example.com')")
            other = second.new_page()
            other.goto(base + '/cs/chat/test-shop')
            expect(other.locator('#who')).to_have_text('buyer@example.com')
            expect(other.locator('#log')).to_contain_text('SAMPLE-1')
            assert other.evaluate("sessionStorage.getItem('h5v:test-shop')") is None
            assert not page.request.get(base + '/cs/chat/test-shop/session', params={'visitor': guest}).ok
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
