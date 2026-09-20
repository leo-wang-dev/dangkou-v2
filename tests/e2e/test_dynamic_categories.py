from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import expect

from audit.test_browser_round2 import TOKEN, browser, page, server, site
from catalog import tickets
from catalog.dynamic_import import build_ticket_payload
from tests.test_workbook_templates import blowdryer_fixture


def _template_ticket(site):
    root = Path(site['folder']) / 'dynamic-fixture'
    root.mkdir(exist_ok=True)
    payload = build_ticket_payload(
        site['conn'], blowdryer_fixture(root), root / 'work', source_key='merchant-sheet')
    return tickets.create(site['conn'], 'template_import', None, payload), payload


def test_b_template_import_confirms_fields_then_exposes_dynamic_category(page, site):
    ticket, payload = _template_ticket(site)
    page.goto(site['base'] + '/?t=' + TOKEN)

    card = page.locator('.card').filter(has_text='分类模板与商品导入')
    expect(card).to_be_visible()
    card.get_by_role('button', name='展开明细').click()
    detail = page.locator(f'#detail-{ticket["id"]}')
    expect(detail).to_contain_text('吹风机')
    expect(detail).to_contain_text('产品型号')
    expect(detail.locator('[data-template-visibility]')).to_have_count(11)
    cost_visibility = detail.locator('[data-field-label-name="不含税 单风嘴成本"]')
    expect(cost_visibility).to_have_value('internal')
    first_row = detail.locator('.draft').first
    first_row.get_by_role('button', name='编辑', exact=True).click()
    page.locator('#fg-field_070a8016fc').fill('审批红色')
    page.locator('#modalBox').get_by_role('button', name='提交', exact=True).click()
    expect(first_row).to_contain_text('审批红色')

    card.get_by_role('button', name='整单通过', exact=True).click()
    expect(page.locator('#review')).to_contain_text('没有待办工单')

    page.get_by_role('button', name='商品', exact=True).click()
    page.locator('#catTabs').get_by_role('button', name='吹风机', exact=True).click()
    expect(page.locator('#plist')).to_contain_text('戴森款HD15')
    expect(page.locator('#plist')).to_contain_text('审批红色')
    expect(page.locator('#plist .ptitle').first).not_to_have_text('1')
    expect(page.locator('#plist')).to_contain_text('不含税 单风嘴成本')

    with page.expect_download() as download:
        page.get_by_role('button', name='下载吹风机模板', exact=True).click()
    saved = Path(site['folder']) / download.value.suggested_filename
    download.value.save_as(saved)
    sheet = load_workbook(saved).active
    assert sheet.title == '吹风机'
    assert [cell.value for cell in sheet[2]][:4] == ['序号', '产品型号', '颜色', '图片']


def test_reused_template_edits_create_a_new_version(site):
    first, payload = _template_ticket(site)
    tickets.decide(site['conn'], first['id'], first['token'], True)
    second_payload = build_ticket_payload(
        site['conn'], Path(site['folder']) / 'dynamic-fixture' / '成本报价单.xlsx',
        Path(site['folder']) / 'dynamic-fixture' / 'second', source_key='merchant-sheet')
    assert second_payload['sheets'][0]['template_action'] == 'reuse'
    category = payload['sheets'][0]['template']['key']
    edited = second_payload['sheets'][0]['template']
    edited['fields'][2]['visibility'] = 'internal'
    second = tickets.create(site['conn'], 'template_import', None, second_payload)
    tickets.decide(site['conn'], second['id'], second['token'], True,
                   {'templates': {category: edited}})
    current = site['conn'].execute(
        'SELECT version,fields_json FROM category_template WHERE key=?', (category,)).fetchone()
    assert current['version'] == 2
    assert '"visibility": "internal"' in current['fields_json']
