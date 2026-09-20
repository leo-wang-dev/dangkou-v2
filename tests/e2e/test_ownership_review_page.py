import socket
import threading
import time
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn
from catalog import db,merchant_onboarding as hub,merchant_binding as binding


def test_register_match_and_manual_confirmation_browser(tmp_path,monkeypatch):
    monkeypatch.setenv('MERCHANT_HUB_DB',str(tmp_path/'hub.db'))
    monkeypatch.setenv('MERCHANT_HUB_ENABLED','1')
    monkeypatch.setenv('ONBOARDING_ALLOW_HTTP_TEST','1')
    source=db.connect(str(tmp_path/'shop.db'));db.init_db(source);source.close()
    c=hub.connect();token=hub.new_invite(c,str(tmp_path/'shop.db'))
    hub.handle(c,'123','绑定已有档口 '+token);c.commit()
    app=FastAPI();app.state.token='fixture-admin';binding.register(app)
    app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[2]/'static',html=True))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto(f'http://127.0.0.1:{port}/merchant/review.html')
            page.get_by_label('审核凭证',exact=True).fill('fixture-admin');page.get_by_role('button',name='打开审核列表').click()
            page.get_by_label('原登记联系方式类型',exact=True).select_option('wechat')
            page.get_by_label('原登记联系方式',exact=True).fill('original_fixture_owner')
            page.get_by_label('原始资料来源或凭据编号',exact=True).fill('historic-contact-record-2026')
            page.get_by_label('审核人',exact=True).fill('fixture-operator')
            page.get_by_role('button',name='保存原始登记资料').click()
            page.get_by_text('已保存。请申请人在 bot 发送“核对联系方式 原登记号码或微信号”；保存新底册会使旧匹配失效。',exact=True).wait_for()
            assert '登记资料匹配' in hub.handle(c,'123','核对联系方式 original_fixture_owner');c.commit()
            page.get_by_role('button',name='刷新申请').click()
            page.get_by_text('登记资料已匹配，仍需联系本人确认。',exact=True).wait_for()
            page.get_by_label('本次核验记录编号或说明',exact=True).fill('fixture-original-wechat-confirmation')
            page.get_by_role('button',name='批准认领').click()
            page.get_by_text('须先通过原登记联系方式确认账号持有人，号码匹配不等于身份认证',exact=True).wait_for()
            page.get_by_label('已通过上述原登记电话或微信确认申请人本人',exact=True).check()
            page.get_by_role('button',name='批准认领').click()
            page.get_by_text('已批准。申请人回复“认证进度”即可继续接入。',exact=True).wait_for()
            assert '档口名称' in hub.handle(c,'123','认证进度')
            assert not errors
            browser.close()
    finally:server.should_exit=True;thread.join(timeout=10);sock.close();c.close()
