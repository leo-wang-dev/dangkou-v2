"""Click through the real customer's photos after the bot has confirmed the list."""
import io
import json
from pathlib import Path
import openpyxl
import pytest
from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, ART, ROOT
from catalog.csbot import CsBot
from scripts.verify_customer_photos import ReplayModel


def _photos_available():
    dataset = json.loads((ROOT / 'tests/fixtures/customer_photos.json').read_text())
    return all((Path(dataset['source_dir']) / c['file']).is_file() for c in dataset['cases'])


@pytest.mark.skipif(not _photos_available(), reason='真实客户照片数据集只在采集机器上（绝对路径随机器），无图时跳过')
def test_customer_real_photos_edit_and_download(page, site):
    dataset=json.loads((ROOT/'tests/fixtures/customer_photos.json').read_text())
    images={c['id']:(Path(dataset['source_dir'])/c['file']).read_bytes() for c in dataset['cases']}
    model=ReplayModel(dataset['cases'],images)
    bot=CsBot(site['conn'],None,llm=model,notifier=Mock(),img_dir=str(site['photo'].parent))
    cust=bot._ensure_customer({'id':'987'})
    for case in dataset['cases']:
        bot._on_photo(cust,None,prepared=bot._prepare_photo(cust,images[case['id']]))
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
    from catalog import agent, tickets
    from catalog.dynamic_import import build_ticket_payload
    from audit.test_browser_round2 import review
    from openpyxl import Workbook
    path=site['folder']/'source.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='冲突品类'
    ws.append(['产品型号','价格']); ws.append(['CONCURRENT','10']); wb.save(path)

    def fake_parse(template,xlsx,work_dir,*,sheet=''):
        keys={f['label']:f['key'] for f in template['fields']}
        return {'vendor':None,'products':[{keys['产品型号']:'CONCURRENT',keys['价格']:'10'}]}
    monkeypatch.setattr(agent,'parse_dynamic',fake_parse)
    pending=[]
    for _ in range(2):
        payload=build_ticket_payload(site['conn'],path,site['folder']/'work',source_key='browser-conflict')
        tk=tickets.create(site['conn'],'template_import',None,payload)
        pending.append(dict(site['conn'].execute('SELECT * FROM approval_ticket WHERE id=?',(tk['id'],)).fetchone()))
    first,second=pending
    assert site['api'].post(f"/tickets/{first['id']}/decision",json={'token':first['token'],'approved':True}).status_code==200
    detail=review(page,site,second)
    with page.expect_response(lambda r:'/decision' in r.url) as response:
        detail.locator('..').get_by_role('button',name='整单通过',exact=True).click()
    assert response.value.status==409
    assert '已变化' in response.value.json()['detail']
    assert site['conn'].execute("SELECT COUNT(*) FROM product_dynamic WHERE json_extract(data_json,'$.model')='CONCURRENT'").fetchone()[0]==1
    assert site['conn'].execute('SELECT status FROM approval_ticket WHERE id=?',(second['id'],)).fetchone()[0]=='pending'
