"""Extra clicks covering existing-product import edits, not only new import rows."""
from playwright.sync_api import expect
from catalog import tickets
from audit.test_browser_round2 import server,site,browser,page,review,submit


def make_update(site, images=False):
    old = dict(site['conn'].execute("SELECT * FROM product_curler WHERE id='c1'").fetchone())
    new = {'item_no':'CURLER-1','price':'12'}
    if images:
        new.update(images=[site['photo'].name],image_main=site['photo'].name)
    return tickets.create(site['conn'],'import','curler',{'kind':'import',
        'work_dir':str(site['photo'].parent),'drafts':{'new':[],'update':[[old,new]],'delist':[]}})


def test_import_edit_retains_tiers_and_visibility(page,site):
    tk = make_update(site)
    detail = review(page,site,tk)
    detail.get_by_role('button',name='编辑',exact=True).click()
    expect(page.locator('#fg-tier_price')).to_have_count(0)
    expect(page.locator('#fg-cs_visible')).to_have_value('1')
    page.locator('#fg-price').fill('14')
    submit(page)
    with page.expect_response(lambda r: r.url.endswith(f'/tickets/{tk["id"]}/row') and r.request.method=='POST') as approval:
        detail.get_by_role('button',name='✓通过',exact=True).click()
    assert approval.value.status == 200
    row = site['conn'].execute("SELECT price,tier_price,cs_visible FROM product_curler WHERE id='c1'").fetchone()
    assert tuple(row) == ('14','20:12;50:11',1)


def test_update_import_shows_new_photo_before_approval(page,site):
    tk = make_update(site,images=True)
    detail = review(page,site,tk)
    expect(detail.locator('img.p')).to_have_attribute('src',f'/ticketimg/{tk["id"]}/{site["photo"].name}?t={tk["token"]}')
    expect(detail.locator('img.p')).to_be_visible()
    assert detail.locator('img.p').evaluate('(image)=>image.naturalWidth>0')
