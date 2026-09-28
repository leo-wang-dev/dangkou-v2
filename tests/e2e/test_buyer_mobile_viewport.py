"""The real chat composer follows the visual viewport used by mobile browser bars."""
import socket
import threading
import time

import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from tests.test_h5_transactions import h5
from tests.test_userapp import client as userapp_client


def test_chat_input_stays_above_bottom_bar_and_keyboard(h5):
    app, _, _ = h5
    from pathlib import Path
    app.mount('/', StaticFiles(directory=str(Path(__file__).resolve().parents[2] / 'static'), html=True))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]})
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        assert server.started
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            page.add_init_script('''
                const listeners = [];
                const viewport = {height: 460, offsetTop: 0,
                    addEventListener: (name, fn) => listeners.push(fn),
                    removeEventListener: () => {}};
                Object.defineProperty(window, 'visualViewport', {value: viewport, configurable: true});
                window.setVisibleViewport = (height, offsetTop=0) => {
                    viewport.height = height; viewport.offsetTop = offsetTop;
                    listeners.forEach(fn => fn());
                };
            ''')
            page.goto(f'http://127.0.0.1:{port}/cs/chat/test-shop')
            page.locator('#text').wait_for()

            def bottom():
                return page.locator('form').bounding_box()['y'] + page.locator('form').bounding_box()['height']

            assert bottom() <= 460
            page.evaluate('setVisibleViewport(300)')
            assert bottom() <= 300
            page.evaluate('setVisibleViewport(650,40)')
            assert 600 <= bottom() <= 690
            page.locator('#text').click()
            assert page.locator('form button').is_visible()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def test_central_tool_actions_stay_above_bottom_bar(userapp_client):
    app = userapp_client.app
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]})
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        assert server.started
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            page.add_init_script('''
                const listeners = [];
                const viewport = {height: 460, addEventListener: (name, fn) => listeners.push(fn), removeEventListener: () => {}};
                Object.defineProperty(window, 'visualViewport', {value: viewport, configurable: true});
                window.setVisibleViewport = height => {viewport.height = height; listeners.forEach(fn => fn())};
            ''')
            page.goto(f'http://127.0.0.1:{port}/')
            page.locator('nav').wait_for()
            def bottom():
                rect = page.locator('nav').bounding_box()
                return rect['y'] + rect['height']
            assert bottom() <= 460
            page.evaluate('setVisibleViewport(350)')
            assert bottom() <= 350
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
