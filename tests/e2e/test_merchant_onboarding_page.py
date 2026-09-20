"""Real browser form clicks with deterministic Telegram identity transport."""
import json
from pathlib import Path
import threading
import socket
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
from catalog import merchant_binding as binding,merchant_onboarding as hub


def test_verify_confirm_bind_browser(tmp_path,monkeypatch):
    monkeypatch.setenv('MERCHANT_HUB_DB',str(tmp_path/'hub.db'))
    monkeypatch.setenv('MERCHANT_RUNTIME_DIR',str(tmp_path/'shops'))
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
    monkeypatch.setenv('ONBOARDING_ALLOW_HTTP_TEST','1')
    monkeypatch.setattr(binding,'identity',lambda token:{'id':222222,'username':'fixture_shop_bot','is_bot':True})
    c=hub.connect();hub.handle(c,'123','创建新档口')
    for value in ['页面测试店','@owner_name','wechat_owner',*(['跳过']*7)]:hub.handle(c,'123',value)
    hub.handle(c,'123','确认配置')
    key='b'*40
    c.execute('UPDATE merchant SET binding_hash=?,binding_expires=9999999999',(hub.digest(key),));c.commit()
    app=FastAPI();binding.register(app)
    app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[2]/'static',html=True))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}/merchant/connect.html?k={key}')
            assert '?k=' not in page.url
            page.get_by_label('Bot Token').fill('222222:'+('x'*35))
            page.get_by_role('button',name='验证 bot 身份').click()
            page.get_by_role('button',name='确认绑定此客服 bot').wait_for()
            assert '@fixture_shop_bot' in page.locator('#status').inner_text()
            assert hub.account(c,'123')['bot_id'] is None
            page.get_by_role('button',name='确认绑定此客服 bot').click()
            page.locator('#form').wait_for(state='hidden')
            assert '启用客服' in page.locator('#status').inner_text()
            assert hub.account(c,'123')['bot_id']=='222222'
            assert not errors
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close();c.close()
