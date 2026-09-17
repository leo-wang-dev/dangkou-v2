"""E2E 页面点击：真实起服（临时库+随机端口）→ Playwright 点清单页/红线审批卡 → 断言落库。"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='module')
def server():
    """起真实 uvicorn 子进程：临时库 + 随机端口；退出断言端口释放（Harness 复原）。"""
    tmp = tempfile.mkdtemp(prefix='cs-e2e-')
    port = 18890
    env = {**os.environ, 'CATALOG_V2_DB': os.path.join(tmp, 'e2e.db'),
           'CATALOG_V2_IMG': os.path.join(tmp, 'img'),
           'CATALOG_V2_SERVICE_TOKEN': ''}          # e2e 关鉴权（真实部署有 token）
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'catalog.main:app', '--port', str(port)],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'
    for _ in range(60):                              # 等就绪
        try:
            urllib.request.urlopen(base + '/health', timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError('服务没起来')
    yield base, os.path.join(tmp, 'e2e.db')
    proc.terminate()
    proc.wait(timeout=10)
    import socket
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # TIME_WAIT 不算占用
    try:                                             # 复原断言：端口必须释放（活监听=失败）
        s.bind(('127.0.0.1', port))
    finally:
        s.close()
    subprocess.run(['rm', '-rf', tmp], check=False)  # 复原断言：临时库删除


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO cs_customer(id, tg_id, tg_name) VALUES('c1','100','买家')")
    conn.execute("INSERT INTO cs_note(customer_id, photo, fields_json, status) VALUES("
                 "'c1','', '{\"型号或品名\":\"直发夹板\",\"价格\":\"80R\",\"颜色\":\"黑色\"}', 'confirmed')")
    conn.execute("INSERT INTO cs_link(token, customer_id) VALUES('e2etoken', 'c1')")
    conn.commit()
    conn.close()


def test_list_page_click_edit_export(server):
    from playwright.sync_api import sync_playwright
    base, db_path = server
    _seed(db_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f'{base}/cs/list.html?k=e2etoken')
        page.wait_for_selector('table')
        # 点击单元格改价（contenteditable）→ blur 保存
        cell = page.locator('td[data-field="价格"]')
        assert cell.text_content().strip() == '80R'
        cell.click()
        cell.fill('2.5')
        cell.blur()
        page.wait_for_timeout(600)
        # 刷新后持久化
        page.reload()
        page.wait_for_selector('table')
        assert page.locator('td[data-field="价格"]').text_content().strip() == '2.5'
        # 点导出 → 下载 xlsx → 内容断言
        with page.expect_download() as dl:
            page.click('button:has-text("导出")')
        path = dl.value.path()
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(open(path, 'rb').read()))
        heads = [c.value for c in wb.active[1]]
        row2 = [c.value for c in wb.active[2]]
        assert '价格' in heads and '型号或品名' in heads
        assert row2[heads.index('价格')] == '2.5'
        browser.close()


def test_redline_card_approve_effect(server):
    from playwright.sync_api import sync_playwright
    base, db_path = server
    # 走真实 API 建审批单（=微信 AI 工具调用同款入口）
    req = urllib.request.Request(f'{base}/cs/redline', method='POST',
                                 data=json.dumps({'text_raw': '数量少于50的转人工'}).encode(),
                                 headers={'Content-Type': 'application/json'})
    tid = json.load(urllib.request.urlopen(req))['ticket_id']
    tickets = json.load(urllib.request.urlopen(f'{base}/tickets'))['tickets']
    t = [x for x in tickets if x['id'] == tid][0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/cs/redline.html?i={tid}&t={t['token']}")
        page.wait_for_selector('.new')
        # 审批卡显示 旧文→新文
        assert '50' in page.locator('.new').text_content()
        assert '20' in page.locator('.old').text_content()      # 默认文案(旧)含20
        # 点批准 → 生效
        page.click('button:has-text("批准")')
        page.wait_for_selector('#done:not([style*="display: none"])', state='visible')
        assert '已批准' in page.locator('#done').text_content()
        browser.close()
    after = json.load(urllib.request.urlopen(f'{base}/cs/redline'))
    assert after['text_raw'] == '数量少于50的转人工'            # 页面点击 → 库里生效
