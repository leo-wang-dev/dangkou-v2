"""Real browser + HTTP + file SQLite; only model output is replaced."""
import json
import os
import socket
import threading
import time
from pathlib import Path
import uvicorn
from playwright.sync_api import sync_playwright, expect
from catalog import llm
from tests.test_h5_transactions import h5


def test_issued_photo_intent_and_explicit_card_association(h5, monkeypatch):
    app,database,photo=h5
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=str(Path(__file__).resolve().parents[2]/'static'),html=True),name='static')
    calls=[]
    def vision(*args,**kwargs):
        calls.append(1)
        return json.dumps([{'型号或品名':'Browser Goods'},{'名片':{'档口名称':'Browser Card'}}])
    monkeypatch.setattr(llm,'chat_vision',vision)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        assert server.started
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':390,'height':844})
            page.goto(f'http://127.0.0.1:{port}/cs/chat/test-shop')
            expect(page.locator('#photo-mode')).to_have_text('图片模式：待选择')
            with page.expect_response(lambda r:r.url.endswith('/photo')) as response:
                page.locator('#file').set_input_files({'name':'goods.jpg','mimeType':'image/jpeg','buffer':photo})
            assert response.value.json()['status']=='intent_required'
            assert not calls
            page.get_by_role('button',name='记笔记',exact=True).click()
            expect(page.locator('#photo-mode')).to_have_text('图片模式：记笔记')
            expect(page.locator('#batches input')).to_have_count(1)
            page.locator('#batches input').check()
            page.get_by_role('button',name='确认切换：Browser Card',exact=True).click()
            expect(page.locator('#batches input')).to_have_count(0)
            expect(page.get_by_role('button',name='当前档口：Browser Card',exact=True)).to_be_visible()
            page.get_by_role('button',name='📋 我的清单',exact=True).click()
            expect(page.frame_locator('#listframe').locator('td[data-field="档口名称"]')).to_have_text('Browser Card')
            page.get_by_role('button',name='✕ 关闭',exact=True).click()
            page.once('dialog',lambda dialog:dialog.accept('Manual Shop'))
            page.get_by_role('button',name='手动切换档口',exact=True).click()
            expect(page.get_by_role('button',name='当前档口：Manual Shop',exact=True)).to_be_visible()
            artifact=Path(os.environ.get('DANGKOU_AUDIT_ARTIFACT_DIR','/tmp/dangkou-task4-audit'));artifact.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(artifact/'guest-photo-session.png'),full_page=True)
            page.get_by_role('button',name='结束当前会话',exact=True).click()
            expect(page.locator('#photo-mode')).to_have_text('图片模式：待选择')
            expect(page.locator('#batches')).to_be_empty()
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10)
        assert not thread.is_alive()
