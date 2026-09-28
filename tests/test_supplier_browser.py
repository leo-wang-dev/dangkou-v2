"""Real browser/API regression for merchant supplier and template controls."""
from playwright.sync_api import expect
from audit.test_browser_round2 import server, site, browser, page, products, submit, TOKEN
from catalog import dynamic_import, tickets, dynamic_catalog


def test_merchant_template_type_delete_and_supplier_action(page, site):
    payload = dynamic_import.manual_template_payload('可编辑模板', [{'label':'型号'}, {'label':'删除这列'}])
    ticket = tickets.create(site['conn'], 'template_import', '', payload)
    site['conn'].commit()
    page.goto(site['base'] + '/?t=' + TOKEN)
    page.evaluate('loadTickets()')
    page.get_by_role('button', name='展开明细 ▾').first.click()
    section = page.locator('.template-section')
    section.locator('tr[data-template-field]').last.get_by_role('button',name='✕').click()
    section.get_by_role('button', name='＋ 添加表头字段').click()
    added = section.locator('tr[data-template-field]').last
    added.locator('[data-field-label]').fill('补充图片')
    added.locator('[data-template-type]').select_option('image')
    decisions = page.evaluate(f'collectTemplateDecisions({ticket["id"]})')
    template = next(iter(decisions.values()))
    assert [f['label'] for f in template['fields']] == ['型号','补充图片']
    assert template['fields'][1]['role'] == 'image'
    response = site['api'].post(f'/tickets/{ticket["id"]}/decision', json={'token':ticket['token'],'approved':True,'decisions':{'templates':decisions}})
    assert response.status_code == 200
    assert dynamic_catalog.get_template(site['conn'], template['key'])['fields'][1]['role'] == 'image'
    products(page,site)
    # Prompt supplies category default; saving a product has a separate supplier field.
    page.evaluate("window.prompt = () => '厂默认'")
    page.locator('#supplier-cat').click()
    expect(page.locator('#supplier-cat')).to_contain_text('厂默认')
    page.locator('#plist').get_by_role('button', name='编辑',exact=True).first.click()
    page.locator('#fg-supplier').fill('独立厂')
    submit(page)
    assert site['api'].get('/products/cat_a').json()['products'][0]['supplier'] == '独立厂'
