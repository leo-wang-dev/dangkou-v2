"""Click through the real customer's photos after the bot has confirmed the list."""
import io
import json
from pathlib import Path
from unittest.mock import Mock

import openpyxl
from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, ART, ROOT
from catalog.csbot import CsBot
from scripts.verify_customer_photos import ReplayModel


def test_customer_real_photos_edit_and_download(page, site):
    dataset=json.loads((ROOT/'tests/fixtures/customer_photos.json').read_text())
    images={c['id']:(Path(dataset['source_dir'])/c['file']).read_bytes() for c in dataset['cases']}
    api=Mock();model=ReplayModel(dataset['cases'],images)
    bot=CsBot(site['conn'],api,llm=model,notifier=Mock(),img_dir=str(site['photo'].parent))
    for i,case in enumerate(dataset['cases'],700):
        api.download_photo.return_value=images[case['id']]
        bot.handle_update({'update_id':i,'message':{'chat':{'id':987},'from':{'id':987},'photo':[{'file_id':'f','width':1280}]}})
    cust=dict(site['conn'].execute("SELECT * FROM cs_customer WHERE tg_id='987'").fetchone())
    bot._confirm_drafts(cust);bot._make_link(cust)
    token=site['conn'].execute('SELECT token FROM cs_link WHERE customer_id=?',(cust['id'],)).fetchone()[0]
    page.set_viewport_size({'width':390,'height':844})
    page.goto(site['base']+'/cs/list.html?k='+token)
    expect(page.locator('table tr')).to_have_count(6)
    expect(page.locator('table')).to_contain_text('350mL')
    expect(page.locator('table')).to_contain_text('260g')
    expect(page.locator('table img')).to_have_count(5)
    page.wait_for_function("[...document.querySelectorAll('table img')].every(i=>i.complete && i.naturalWidth>0)")
    price=page.locator('[data-field="价格"]').first
    price.fill('2.5');page.locator('h1').click()
    expect(page.locator('#tip')).to_contain_text('已保存')
    page.reload();expect(page.locator('[data-field="价格"]').first).to_have_text('2.5')
    with page.expect_download() as download:
        page.get_by_role('button',name='⬇️ 导出 Excel').click()
    path=ART/'real-customer-click-export.xlsx';download.value.save_as(str(path))
    ws=openpyxl.load_workbook(path).active
    assert ws.max_row==6 and len(ws._images)==5
    assert any(cell.value=='2.5' for row in ws for cell in row)


def test_stale_import_approval_shows_conflict_without_duplicates(page,site,monkeypatch):
    from catalog import ingest
    from catalog.storage import LocalStorage
    from tests.test_ingest import _wait_done
    from audit.test_browser_round2 import review
    monkeypatch.setattr(ingest.agent,'parse',lambda *a:{'products':[{'model_no':'CONCURRENT','price':'10'}]})
    pending=[]
    for _ in range(2):
        doc=ingest.start(site['conn'],LocalStorage(str(site['folder']/'images')),str(site['folder']/'source.xlsx'),'razor',source_key='browser-conflict')
        assert _wait_done(site['conn'],doc)['status']=='ticketed'
        pending.append(dict(site['conn'].execute("SELECT * FROM approval_ticket WHERE json_extract(payload,'$.doc_id')=?",(doc,)).fetchone()))
    first,second=pending
    assert site['api'].post(f"/tickets/{first['id']}/decision",json={'token':first['token'],'approved':True}).status_code==200
    detail=review(page,site,second)
    with page.expect_response(lambda r:'/decision' in r.url) as response:
        detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    assert response.value.status==409
    assert '已变化' in response.value.json()['detail']
    assert site['conn'].execute("SELECT COUNT(*) FROM product_razor WHERE model_no='CONCURRENT'").fetchone()[0]==1
    assert site['conn'].execute('SELECT status FROM approval_ticket WHERE id=?',(second['id'],)).fetchone()[0]=='pending'
