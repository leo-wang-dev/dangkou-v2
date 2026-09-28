"""Actual localized static controls + HTTP/SQLite, with offline vision only."""
import io
import json
import socket
import threading
import time
from pathlib import Path
import openpyxl
import uvicorn
from playwright.sync_api import sync_playwright,expect
from catalog import llm,cs_i18n
from tests.test_h5_transactions import h5


def test_french_and_arabic_photo_batch_and_export(h5,monkeypatch):
    app,_,photo=h5
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=str(Path(__file__).resolve().parents[2]/'static'),html=True))
    monkeypatch.setattr(llm,'chat_vision',lambda *a,**kw:json.dumps([{'型号或品名':'KS-1100'},{'名片':{'档口名称':'ACME Ltd','供应商联系方式':'+86 13800138000'}}]))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for code in ('fr','ar'):
                page=browser.new_page(viewport={'width':420,'height':920})
                page.goto(f'http://127.0.0.1:{port}/cs/chat/test-shop')
                page.locator('#customer-language').select_option(code)
                expect(page.locator('html')).to_have_attribute('lang',code)
                expect(page.locator('html')).to_have_attribute('dir','rtl' if code=='ar' else 'ltr')
                expect(page.get_by_role('button',name=cs_i18n.t('takeNotes',code),exact=True)).to_be_visible()
                page.locator('#file').set_input_files({'name':'KS-1100.jpg','mimeType':'image/jpeg','buffer':photo})
                expect(page.locator('#pending-photo')).to_be_visible()
                expect(page.locator('#log')).to_contain_text(cs_i18n.t('photoIntentPrompt',code))
                page.get_by_role('button',name=cs_i18n.t('takeNotes',code),exact=True).click()
                expect(page.locator('#pending-photo')).to_be_hidden()
                expect(page.locator('#log')).to_contain_text(cs_i18n.t('recorded',code))
                page.locator('#batches input').check()
                page.get_by_role('button',name=cs_i18n.t('confirmSwitch',code)+'ACME Ltd',exact=True).click()
                expect(page.locator('#batches input')).to_have_count(0)
                page.locator('#mylist').click()
                frame=page.frame_locator('#listframe')
                expect(frame.locator('#export')).to_have_text('⬇️ '+cs_i18n.t('exportExcel',code))
                expect(frame.locator('td[data-field="档口名称"]')).to_have_text('ACME Ltd')
                with page.expect_download() as download:frame.locator('#export').click()
                ws=openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
                assert bool(ws.sheet_view.rightToLeft)==(code=='ar')
                assert 'KS-1100' in [c.value for row in ws for c in row]
                page.screenshot(path=f'/tmp/task5-{code}-static.png',full_page=True)
                page.close()
            browser.close()
    finally:
        server.should_exit=True;thread.join(10);assert not thread.is_alive()


def test_compiled_h5_under_tool_french_arabic(h5,monkeypatch):
    import os
    import pytest
    dist=os.environ.get('DANGKOU_TEST_H5_DIST')
    if not dist:pytest.skip('Run with fresh H5 build path in DANGKOU_TEST_H5_DIST')
    app,_,photo=h5
    from fastapi.staticfiles import StaticFiles
    app.mount('/tool',StaticFiles(directory=dist,html=True))
    monkeypatch.setattr(llm,'chat_vision',lambda *a,**kw:json.dumps([{'型号或品名':'KS-1100'}]))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for code in ('fr','ar'):
                context=browser.new_context(viewport={'width':420,'height':920},accept_downloads=True)
                context.add_init_script(f"localStorage.setItem('dk_lang','{code}')")
                page=context.new_page();requests=[];errors=[]
                page.on('request',lambda request:requests.append(request.url));page.on('pageerror',lambda error:errors.append(str(error)))
                base=f'http://127.0.0.1:{port}'
                page.goto(base+'/tool/#/pages/chat/chat?token=test-shop')
                page.wait_for_function("sessionStorage.getItem('h5v:test-shop')")
                expect(page.locator('html')).to_have_attribute('lang',code)
                expect(page.get_by_text(cs_i18n.t('takeNotes',code),exact=True)).to_be_visible()
                visitor=page.evaluate("sessionStorage.getItem('h5v:test-shop')")
                response=page.request.post(base+'/cs/chat/test-shop/photo',headers={'X-Customer-Language':code},multipart={'visitor':visitor,'file':{'name':'p.jpg','mimeType':'image/jpeg','buffer':photo}})
                assert response.json()['status']=='intent_required'
                page.reload()
                expect(page.get_by_text(cs_i18n.t('retryPhoto',code),exact=True)).to_be_visible()
                page.get_by_text(cs_i18n.t('takeNotes',code),exact=True).click()
                expect(page.get_by_text(cs_i18n.t('retryPhoto',code),exact=True)).to_have_count(0)
                expect(page.locator('.log')).to_contain_text(cs_i18n.t('recorded',code))
                page.get_by_text('📋 '+cs_i18n.t('myShortList',code),exact=True).first.click()
                expect(page.get_by_text('⬇️ '+cs_i18n.t('exportExcel',code),exact=True)).to_be_visible()
                with page.expect_download() as download:page.get_by_text('⬇️ '+cs_i18n.t('exportExcel',code),exact=True).click()
                ws=openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
                assert bool(ws.sheet_view.rightToLeft)==(code=='ar')
                assert not errors,errors
                assert not any('/tool/cs/' in url for url in requests),requests
                assert any('/cs/chat/test-shop/mode' in url for url in requests)
                page.screenshot(path=f'/tmp/task5-{code}-compiled.png',full_page=True)
                context.close()
            browser.close()
    finally:
        server.should_exit=True;thread.join(10);assert not thread.is_alive()
