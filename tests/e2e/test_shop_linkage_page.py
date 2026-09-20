"""Local B/C integration: real approval clicks/API/SQLite/export; no vendor traffic."""
import io
from unittest.mock import Mock
import openpyxl
from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, review
from catalog.csbot import CsBot
from catalog import shop_link


def test_merchant_approval_to_customer_shop_and_excel(page,site):
    api=site['api'];conn=site['conn']
    tk=api.patch('/shop',json={'changes':{'shop_name':'联动测试档口','stall_no':'测试A123','tg_bot_id':'12345','owner_wechat':'TEST-OWNER-WX'}}).json()
    detail=review(page,site,{'id':tk['ticket_id']})
    expect(detail).to_contain_text('联动测试档口')
    detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    shop_link.verify_bot(conn,{'id':12345,'is_bot':True})
    # Merchant changes a real catalog tier through approval; C bot sees the same DB.
    tk=api.patch('/products/curler/c1',json={'changes':{'可观测':'1'}}).json()
    detail=review(page,site,{'id':tk['ticket_id']})
    detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')
    transport=Mock();transport.download_photo.return_value=site['photo'].read_bytes()
    llm=Mock();llm.chat_vision.return_value='[{"型号或品名":"CURLER-1","价格":"模糊"}]';llm.chat_text.return_value='<<PASS>>'
    bot=CsBot(conn,transport,llm=llm,img_dir=str(site['folder']/'buyer-photos'))
    bot.handle_update({'update_id':90001,'message':{'chat':{'id':901,'type':'private'},'from':{'id':901},'photo':[{'file_id':'test','width':160}]}})
    customer=conn.execute("SELECT * FROM cs_customer WHERE tg_id='901'").fetchone()
    assert '老板' in bot._on_text(customer,'询价1 60个')
    assert '¥13' not in transport.send_message.call_args.args[1]
    bot._make_link(customer)
    token=conn.execute('SELECT token FROM cs_link WHERE customer_id=?',(customer['id'],)).fetchone()[0]
    page.goto(site['base']+'/cs/list.html?k='+token)
    expect(page.locator('td[data-field="档口名称"]')).to_have_text('联动测试档口')
    expect(page.locator('td[data-field="供应商联系方式"]')).to_contain_text('TEST-OWNER-WX')
    cell=page.locator('td[data-field="档口名称"]');cell.fill('外部采购测试档口');cell.blur()
    expect(page.locator('#tip')).to_have_text('已保存 ✓')
    expect(page.locator('td[data-field="供应商联系方式"]')).to_have_text('待补充')
    page.reload();expect(page.locator('td[data-field="档口名称"]')).to_have_text('外部采购测试档口')
    expect(page.locator('td[data-field="档口归属依据"]')).to_have_text('客户填写')
    with page.expect_download() as download:
        page.get_by_role('button',name='⬇️ 导出 Excel').click()
    from pathlib import Path
    sheet=openpyxl.load_workbook(io.BytesIO(Path(download.value.path()).read_bytes())).active
    assert sheet.max_row==2 and len(sheet._images)==1
    assert '外部采购测试档口' in str(list(sheet.values)) and 'TEST-OWNER-WX' not in str(list(sheet.values))
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('td[data-field="档口名称"]')).to_have_text('外部采购测试档口')
