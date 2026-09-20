import sqlite3

import pytest

from catalog import db


@pytest.fixture
def conn():
    value = sqlite3.connect(':memory:')
    value.row_factory = sqlite3.Row
    db.init_db(value)
    yield value
    value.close()


def hairdryer_draft():
    return {
        'key': 'hairdryer',
        'name': '吹风机',
        'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'type': 'text', 'required': False,
             'visibility': 'public', 'searchable': True, 'role': 'model'},
            {'key': 'color', 'label': '颜色', 'type': 'text', 'required': False,
             'visibility': 'public', 'searchable': True, 'role': 'spec'},
            {'key': 'photo', 'label': '图片', 'type': 'image', 'required': False,
             'visibility': 'public', 'searchable': True, 'role': 'image'},
            {'key': 'single_nozzle_cost', 'label': '不含税单风嘴成本', 'type': 'money',
             'required': False, 'visibility': 'internal', 'searchable': False, 'role': 'cost'},
        ],
    }


def test_database_seeds_legacy_categories_through_dynamic_registry(conn):
    from catalog import dynamic_catalog

    keys = {item['key'] for item in dynamic_catalog.list_templates(conn)}
    assert {'razor', 'curler'} <= keys
    assert dynamic_catalog.get_template(conn, 'razor')['storage'] == 'legacy'


def test_approved_template_versions_are_immutable(conn):
    from catalog import dynamic_catalog

    first = dynamic_catalog.approve_template(conn, hairdryer_draft())
    assert first['version'] == 1
    changed = hairdryer_draft()
    changed['fields'] = [*changed['fields'], {
        'key': 'power', 'label': '功率', 'type': 'text', 'required': False,
        'visibility': 'public', 'searchable': True, 'role': 'spec'}]
    second = dynamic_catalog.approve_template(conn, changed, expected_version=1)
    assert second['version'] == 2
    versions = dynamic_catalog.template_versions(conn, 'hairdryer')
    assert [v['version'] for v in versions] == [1, 2]
    assert [f['label'] for f in versions[0]['fields']] == [
        '产品型号', '颜色', '图片', '不含税单风嘴成本']


def test_template_rejects_duplicate_or_unsafe_field_keys(conn):
    from catalog import dynamic_catalog

    draft = hairdryer_draft()
    draft['fields'][1]['key'] = 'model'
    with pytest.raises(ValueError, match='字段 key'):
        dynamic_catalog.approve_template(conn, draft)
    draft = hairdryer_draft()
    draft['fields'][0]['key'] = 'price);drop table x;--'
    with pytest.raises(ValueError, match='字段 key'):
        dynamic_catalog.approve_template(conn, draft)


def test_blank_and_repeated_models_remain_separate_products(conn):
    from catalog import dynamic_catalog

    dynamic_catalog.approve_template(conn, hairdryer_draft())
    result = dynamic_catalog.upsert_approved_products(conn, 'hairdryer', [
        {'id': 'row-1', 'inner_code': 'HD-A', 'data': {'model': 'HD15', 'color': '红色'},
         'images': ['hairdryer/row-1/a.png'], 'source_row': 3, 'row_fingerprint': 'fp-1'},
        {'id': 'row-2', 'inner_code': 'HD-B', 'data': {'model': 'HD15', 'color': '蓝色'},
         'images': [], 'source_row': 4, 'row_fingerprint': 'fp-2'},
        {'id': 'row-3', 'inner_code': 'HD-C', 'data': {'model': '', 'color': '白色'},
         'images': [], 'source_row': 5, 'row_fingerprint': 'fp-3'},
    ], source_key='成本报价单.xlsx', source_sheet='吹风机')
    assert result == {'created': 3, 'updated': 0}
    rows = dynamic_catalog.list_products(conn, 'hairdryer')
    assert [row['data']['model'] for row in rows] == ['HD15', 'HD15', '']
    assert len({row['id'] for row in rows}) == 3


def test_public_product_never_exposes_internal_cost(conn):
    from catalog import dynamic_catalog

    dynamic_catalog.approve_template(conn, hairdryer_draft())
    dynamic_catalog.upsert_approved_products(conn, 'hairdryer', [{
        'id': 'row-1', 'inner_code': 'HD-A', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '红色', 'single_nozzle_cost': '35'},
        'images': [], 'source_row': 3, 'row_fingerprint': 'fp-1',
    }], source_key='成本报价单.xlsx', source_sheet='吹风机')
    row = dynamic_catalog.list_products(conn, 'hairdryer', public_only=True)[0]
    assert row['specs'] == {'产品型号': 'HD15', '颜色': '红色'}
    assert '35' not in str(row)


def test_public_projection_blocks_misclassified_money_and_links(conn):
    from catalog import dynamic_catalog

    draft = hairdryer_draft()
    draft['name'] = '批发价23元'
    draft['fields'].extend([
        {'key': 'wholesale', 'label': '批发价', 'type': 'text', 'required': False,
         'visibility': 'public', 'searchable': False, 'role': 'spec'},
        {'key': 'purchase_reference', 'label': '采购参考', 'type': 'money', 'required': False,
         'visibility': 'public', 'searchable': False, 'role': 'spec'},
        {'key': 'supplier_link', 'label': '商品链接', 'type': 'text', 'required': False,
         'visibility': 'public', 'searchable': False, 'role': 'spec'},
        {'key': 'details', 'label': '详情', 'type': 'text', 'required': False,
         'visibility': 'public', 'searchable': False, 'role': 'spec'},
    ])
    dynamic_catalog.approve_template(conn, draft)
    dynamic_catalog.upsert_approved_products(conn, 'hairdryer', [{
        'id': 'row-unsafe', 'inner_code': 'HD-X', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '红色', 'wholesale': '23',
                 'purchase_reference': '23',
                 'supplier_link': 'https://supplier.example/item',
                 'details': 'supplier.example/item'},
    }])
    row = dynamic_catalog.list_products(conn, 'hairdryer', public_only=True)[0]
    assert row['specs'] == {'产品型号': 'HD15', '颜色': '红色'}
    assert row['category_name'] == '商品'
    assert 'supplier.example' not in str(row) and '23' not in str(row)
