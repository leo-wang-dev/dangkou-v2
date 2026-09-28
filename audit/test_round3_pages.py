"""Extra clicks covering existing-product import edits, not only new import rows."""
from playwright.sync_api import expect
from catalog import tickets
from audit.test_browser_round2 import (server, site, browser, page, review, submit,
                                       CAT_B, NAME_B)


def make_update(site, images=False):
    from catalog.dynamic_import import _source_snapshot
    old = dict(site['conn'].execute(
        "SELECT id, supplier, inner_code, data_json, status, image_main, images_json, cs_visible,"
        " source_key, source_sheet, source_row, row_fingerprint FROM product_dynamic"
        " WHERE id='c1'").fetchone())
    old_row = {'id': old['id'], 'supplier': old['supplier'], 'inner_code': old['inner_code'],
               'data': __import__('json').loads(old['data_json']),
               'status': old['status'], 'images': __import__('json').loads(old['images_json']),
               'cs_visible': old['cs_visible'], 'source_key': old['source_key'],
               'source_sheet': old['source_sheet'], 'source_row': old['source_row'],
               'row_fingerprint': old['row_fingerprint']}
    incoming = {'data': {**old_row['data'], 'price': '12'}, 'images': [],
                'image_main': '', 'row_fingerprint': 'updated-fp'}
    if images:
        incoming.update(images=[site['photo'].name], image_main=site['photo'].name)
    fields = [
        {'key': 'model', 'label': '型号', 'type': 'text', 'visibility': 'public',
         'searchable': True, 'role': 'model', 'required': False},
        {'key': 'price', 'label': '价格', 'type': 'money', 'visibility': 'internal',
         'searchable': False, 'role': 'price', 'required': False}]
    payload = {'kind': 'template_import', 'work_dir': str(site['photo'].parent),
               'source_key': 'browser-update', 'sheets': [{
                   'template': {'key': CAT_B, 'name': NAME_B, 'version': 1,
                                'fields': fields, 'storage': 'dynamic', 'source_sheet': NAME_B},
                   'template_action': 'reuse', 'expected_version': 1, 'title': NAME_B,
                   'header_row': 1, 'image_count': 1 if images else 0,
                   'source_sheet': NAME_B,
                   'source_snapshot': _source_snapshot([old_row]),
                   'drafts': {'new': [], 'update': [[old_row, incoming]], 'delist': []}}]}
    return tickets.create(site['conn'], 'template_import', None, payload)


def test_import_edit_retains_visibility(page, site):
    tk = make_update(site)
    detail = review(page, site, tk)
    detail.get_by_role('button', name='编辑', exact=True).click()
    expect(page.locator('#fg-tier_price')).to_have_count(0)
    expect(page.locator('#fg-cs_visible')).to_have_count(0)  # 可观测走卡片开关
    page.locator('#fg-price').fill('14')
    submit(page)
    with page.expect_response(lambda r: r.url.endswith(f"/tickets/{tk['id']}/decision")
                               and r.request.method == 'POST') as approval:
        page.get_by_role('button', name='整单通过', exact=True).click()
    assert approval.value.status == 200
    row = site['conn'].execute(
        "SELECT json_extract(data_json,'$.price') price, cs_visible FROM product_dynamic"
        " WHERE id='c1'").fetchone()
    assert tuple(row) == ('14', 1)


def test_update_import_shows_new_photo_before_approval(page, site):
    tk = make_update(site, images=True)
    detail = review(page, site, tk)
    expect(detail.locator('img.p')).to_have_attribute('src', f'/ticketimg/{tk["id"]}/{site["photo"].name}?t={tk["token"]}')
    expect(detail.locator('img.p')).to_be_visible()
    assert detail.locator('img.p').evaluate('(image)=>image.naturalWidth>0')
