from audit.test_browser_round2 import server, site, browser, page


def test_customer_catalog_page_is_published(page, site):
    response = page.goto(site['base'] + '/cs/products.html')
    assert response.status == 200
