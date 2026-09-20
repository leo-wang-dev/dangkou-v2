"""Regression tests for one merchant's WeChat/TG catalog boundary."""

import pytest
from openpyxl import Workbook

from catalog import customer_catalog, db, dynamic_import, dynamic_catalog, tickets


def _dynamic_fixture(tmp_path, *, sheet_name='跨端测试类目'):
    """Create a non-predefined Sheet so this suite cannot pass via fixed categories."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(['序号', '产品型号', '颜色', '功率', '备注'])
    sheet.append([1, 'LINK-01', '黑色', '1600W', '测试款'])
    sheet.append([2, 'LINK-02', '白色', '1800W', '测试款'])
    path = tmp_path / f'{sheet_name}.xlsx'
    workbook.save(path)
    return path


@pytest.fixture()
def conn(tmp_path):
    value = db.connect(str(tmp_path / 'catalog.db'))
    db.init_db(value)
    yield value
    value.close()


def test_dynamic_category_is_in_same_merchant_catalog_seen_by_stats_and_customer(conn, tmp_path):
    payload = dynamic_import.build_ticket_payload(
        conn, _dynamic_fixture(tmp_path), tmp_path / 'work', source_key='zero-to-one')
    ticket = tickets.create(conn, 'template_import', None, payload)
    tickets.decide(conn, ticket['id'], ticket['token'], True)
    category = payload['sheets'][0]['template']['key']

    template = dynamic_catalog.get_template(conn, category)
    merchant_rows = dynamic_catalog.list_products(conn, category)
    customer_rows = customer_catalog.local_catalog(conn)['products']
    customer_rows = [row for row in customer_rows if row['_category'] == category]

    assert template['name'] == '跨端测试类目'
    assert len(merchant_rows) == 2
    assert len(customer_rows) == 2
    assert {row['id'] for row in merchant_rows} == {row['id'] for row in customer_rows}
    assert all(row['cs_visible'] == 1 for row in customer_rows)


def test_hidden_product_stays_in_merchant_catalog_but_not_customer_catalog(conn, tmp_path):
    payload = dynamic_import.build_ticket_payload(
        conn, _dynamic_fixture(tmp_path), tmp_path / 'work', source_key='visibility-boundary')
    ticket = tickets.create(conn, 'template_import', None, payload)
    tickets.decide(conn, ticket['id'], ticket['token'], True)
    category = payload['sheets'][0]['template']['key']
    product = dynamic_catalog.list_products(conn, category)[0]
    conn.execute('UPDATE product_dynamic SET cs_visible=0 WHERE id=?', (product['id'],))
    conn.commit()

    merchant_rows = dynamic_catalog.list_products(conn, category)
    customer_rows = [row for row in customer_catalog.local_catalog(conn)['products']
                     if row['_category'] == category]
    assert len(merchant_rows) == 2
    assert len(customer_rows) == 1
    assert product['id'] not in {row['id'] for row in customer_rows}


def test_arbitrary_sheet_name_creates_a_queryable_dynamic_category(conn, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '配件-2026'
    sheet.append(['产品型号', '材质', '颜色'])
    sheet.append(['ACC-1', 'ABS', '黑色'])
    path = tmp_path / 'arbitrary-category.xlsx'
    workbook.save(path)
    payload = dynamic_import.build_ticket_payload(conn, path, tmp_path / 'work', source_key='arbitrary')
    ticket = tickets.create(conn, 'template_import', None, payload)
    tickets.decide(conn, ticket['id'], ticket['token'], True)
    category = payload['sheets'][0]['template']['key']
    assert payload['sheets'][0]['template']['name'] == '配件-2026'
    assert len(dynamic_catalog.list_products(conn, category, public_only=True)) == 1
    assert dynamic_catalog.list_products(conn, 'razor', public_only=True) == []
