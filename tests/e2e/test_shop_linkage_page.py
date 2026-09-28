"""Local B/C integration: real approval clicks/API/SQLite/export; no vendor traffic."""
import io
from unittest.mock import Mock
import openpyxl
from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, review, CAT_B
from catalog.csbot import CsBot
from catalog import shop_link


def test_merchant_approval_to_customer_shop_and_excel(page,site):
    api=site['api'];conn=site['conn']
    tk=api.patch('/shop',json={'changes':{'shop_name':'联动测试档口','stall_no':'测试A123','tg_bot_id':'12345','owner_wechat':'TEST-OWNER-WX'}}).json()
    detail=review(page,site,{'id':tk['ticket_id']})
    expect(detail).to_contain_text('联动测试档口')
    detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    # Merchant changes a real catalog tier through approval; C bot sees the same DB.
    tk=api.patch(f'/products/{CAT_B}/c1',json={'changes':{'可观测':'1'}}).json()
    detail=review(page,site,{'id':tk['ticket_id']})
    detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    llm=Mock();llm.chat_vision.return_value='[{"型号或品名":"MOD-B1","价格":"模糊"}]';llm.chat_text.return_value='<<PASS>>'
    bot=CsBot(conn,None,llm=llm,img_dir=str(site['folder']/'buyer-photos'))
    cust901=bot._ensure_customer({'id':901})
    photo_receipt=bot._on_photo(cust901, None, prepared=bot._prepare_photo(cust901, data=site['photo'].read_bytes()))
    customer=conn.execute("SELECT * FROM cs_customer WHERE tg_id='901'").fetchone()
    ask_reply=bot._on_text(customer,'询价1 60个')
    assert '老板' not in ask_reply
    assert '¥13' not in photo_receipt and '¥13' not in ask_reply   # 价格不泄漏（原 transport.send 断言的内核等价）
    bot._make_link(customer)
    token=conn.execute('SELECT token FROM cs_link WHERE customer_id=?',(customer['id'],)).fetchone()[0]
    page.goto(site['base']+'/cs/list.html?k='+token)
    expect(page.locator('td[data-field="档口名称"]')).to_have_text('联动测试档口')
    expect(page.locator('td[data-field="供应商联系方式"]')).to_contain_text('TEST-OWNER-WX')
    cell=page.locator('td[data-field="档口名称"]');cell.fill('外部采购测试档口');cell.blur()
    expect(page.locator('#tip')).to_have_text('已保存 ✓')
    expect(page.locator('td[data-field="供应商联系方式"]')).to_have_text('待补充')
    page.reload();expect(page.locator('td[data-field="档口名称"]')).to_have_text('外部采购测试档口')
    expect(page.locator('td[data-field="档口归属依据"]')).to_have_count(0)   # 内部留档口径不对客户展示
    assert conn.execute("SELECT source_basis FROM cs_note WHERE customer_id=?",
                        (customer['id'],)).fetchone()[0] == 'manual'
    with page.expect_download() as download:
        page.get_by_role('button',name='⬇️ 导出 Excel').click()
    from pathlib import Path
    sheet=openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
    assert sheet.max_row==3 and len(sheet._images)==1
    assert '外部采购测试档口' in str(list(sheet.values)) and 'TEST-OWNER-WX' not in str(list(sheet.values))
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('td[data-field="档口名称"]')).to_have_text('外部采购测试档口')
