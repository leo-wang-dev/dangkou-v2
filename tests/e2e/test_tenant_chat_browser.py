"""Two-tenant public chat, real browser UI + registered gateway/API/SQLite."""
import io
import os
import socket
import threading
import time
from pathlib import Path

import openpyxl
import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount
from playwright.sync_api import sync_playwright, expect
from catalog import cs_i18n
from tests.test_tenant_chat import tenant_chat, picture


@pytest.mark.parametrize('compiled',[False,True])
def test_two_tenant_chat_browser(tenant_chat, compiled):
    t=tenant_chat
    if compiled:
        dist=os.environ.get('DANGKOU_TEST_H5_DIST')
        if not dist:pytest.skip('Requires fresh compiled H5')
        t.app.router.routes.insert(-1,Mount('/tool',app=StaticFiles(directory=dist,html=True)))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(t.app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]});thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        assert server.started
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            context=browser.new_context(accept_downloads=True)
            context.add_init_script("localStorage.setItem('dk_lang','ar')")
            page=context.new_page();page.set_default_timeout(10000);errors=[];requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:requests.append(request.url))
            page.on('dialog',lambda dialog:dialog.accept())
            base='http://127.0.0.1:'+str(port)
            previous=None
            for index,mid in enumerate(t.mids):
                # The authenticated management UI must produce the public link.
                page.goto(base+'/merchant/manage/'+mid+'/?t=manage-'+str(index))
                page.locator('#v-products').click()
                page.evaluate("navigator.clipboard.writeText=async text=>{window.sharedChat=text}")
                page.get_by_role('button',name='🎧 客服链接',exact=True).click()
                page.wait_for_function('window.sharedChat')
                link=page.evaluate('window.sharedChat');prefix='/merchant/customer/'+mid
                assert link.startswith(base+prefix+'/cs/chat/')
                token=link.rsplit('/',1)[1];session_key='h5v:'+prefix+':'+token
                target=base+'/tool/#/pages/chat/chat?token='+token+'&mid='+mid if compiled else link
                requests.clear();page.goto(target)
                page.wait_for_function('(key)=>sessionStorage.getItem(key)',arg=session_key)
                visitor=page.evaluate('(key)=>sessionStorage.getItem(key)',session_key)
                expect(page.locator('html')).to_have_attribute('dir','rtl')
                # Actual browser multipart upload, then mode control resolves it.
                if compiled:
                    with page.expect_file_chooser() as chooser:
                        page.get_by_text('📷',exact=True).click()
                    chooser.value.set_files({'name':'p.jpg','mimeType':'image/jpeg','buffer':picture()})
                else:page.locator('#file').set_input_files({'name':'p.jpg','mimeType':'image/jpeg','buffer':picture()})
                pending=page.get_by_text(cs_i18n.t('retryPhoto','ar'),exact=True)
                expect(pending).to_be_visible()
                page.get_by_text(cs_i18n.t('takeNotes','ar'),exact=True).click()
                expect(pending).not_to_be_visible()
                expect(page.locator('.log' if compiled else '#log')).to_contain_text(cs_i18n.t('recorded','ar'))
                if compiled:
                    page.get_by_text('📋 '+cs_i18n.t('myShortList','ar'),exact=True).first.click()
                    export=page.get_by_text('⬇️ '+cs_i18n.t('exportExcel','ar'),exact=True)
                    expect(export).to_be_visible()
                    page.wait_for_function("Array.from(document.querySelectorAll('.drawer img')).some(img=>img.naturalWidth>0)")
                else:
                    page.locator('#mylist').click()
                    frame=page.frame_locator('#listframe')
                    expect(frame.locator('td[data-field="型号或品名"]')).to_have_text('MODEL-1')
                    frame.locator('img').first.wait_for()
                    export=frame.locator('#export')
                with page.expect_download() as download:export.click()
                ws=openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
                assert ws.sheet_view.rightToLeft
                assert 'MODEL-1' in [cell.value for row in ws for cell in row]
                assert any(prefix+'/cs/chat/'+token+'/photo' in url for url in requests)
                assert any(prefix+'/cs/link/' in url and '/photo' in url for url in requests)
                assert not any(url.startswith(base+'/cs/') for url in requests),requests
                # Browser-origin capabilities remain unusable against another tenant.
                other='/merchant/customer/'+t.mids[1-index]
                assert page.request.get(base+other+'/cs/chat/'+token).status==404
                if previous:
                    assert page.request.get(link+'/session?visitor='+previous[1]).status in (401,410)
                    assert visitor!=previous[1]
                previous=(token,visitor)
                artifacts=Path(os.environ.get('DANGKOU_AUDIT_ARTIFACT_DIR','/tmp/dangkou-final-fix-audit'));artifacts.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(artifacts/f'tenant-{index}-{"compiled" if compiled else "static"}.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:
        server.should_exit=True;thread.join(10);sock.close();assert not thread.is_alive()
