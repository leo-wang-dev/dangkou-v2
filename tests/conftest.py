"""Use an explicit synthetic template for offline tests, never install it in production data."""
import os
import pytest


# ---- 动态分类夹具：固定品类（razor/curler）删除后，测试统一走生产同款动态链路 ----
# 字段设计覆盖常见断言：型号(model)/价格(price,内部)/规格/颜色/电压/箱规(可出报价单)。
DYNAMIC_FIELDS = [
    {'key': 'model', 'label': '型号', 'type': 'text', 'visibility': 'public',
     'searchable': True, 'role': 'model', 'required': False},
    {'key': 'price', 'label': '价格', 'type': 'money', 'visibility': 'internal',
     'searchable': False, 'role': 'price', 'required': False},
    {'key': 'spec', 'label': '规格', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
    {'key': 'color', 'label': '颜色', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
    {'key': 'voltage', 'label': '电压', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
    {'key': 'ctn', 'label': '箱规', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
]


def seed_category(conn, *, key='test_cat', name='测试品类', fields=None):
    """建（或推进）一个动态分类模板；quote_map 按角色/表头自动推荐。

    幂等：分类已存在且表头一致时直接复用，不推进版本——多条 seed 共用同一分类。
    """
    from catalog import dynamic_catalog
    draft = {'key': key, 'name': name, 'storage': 'dynamic', 'source_sheet': name,
             'fields': fields if fields is not None else DYNAMIC_FIELDS}
    try:
        current = dynamic_catalog.get_template(conn, key)
    except KeyError:
        current = None
    if current is not None:
        same = (current['name'] == name and current['storage'] == 'dynamic'
                and current['fields'] == draft['fields'])
        if same:
            return current
        template = dynamic_catalog.approve_template(conn, draft, expected_version=current['version'])
    else:
        template = dynamic_catalog.approve_template(conn, draft)
    conn.commit()
    return template


def seed_products(conn, rows, *, key='test_cat', name='测试品类', fields=None):
    """rows: [{'id':..(可选), 'data': {字段key: 值}, 'images': [...], 'cs_visible': 1, 'status': 'approved'}, ...]"""
    from catalog import dynamic_catalog, inner_code
    seed_category(conn, key=key, name=name, fields=fields)
    payload = []
    for row in rows:
        payload.append({'id': row.get('id') or inner_code.gen(),
                        'inner_code': row.get('inner_code') or inner_code.gen(),
                        'data': row.get('data') or {}, 'images': row.get('images') or [],
                        'cs_visible': row.get('cs_visible', 1),
                        'status': row.get('status', 'approved')})
    dynamic_catalog.upsert_approved_products(conn, key, payload)
    conn.commit()
    return payload


@pytest.fixture(scope='session')
def synthetic_quote_template(tmp_path_factory):
    from scripts.build_test_quote_template import build
    return str(build(tmp_path_factory.mktemp('quote-fixture') / 'test_quote_template.xlsx'))


@pytest.fixture(autouse=True)
def offline_quote_template(monkeypatch, synthetic_quote_template):
    from catalog import quote
    if not os.environ.get('CATALOG_QUOTE_TEMPLATE'):
        monkeypatch.setattr(quote, 'TEMPLATE_V2_PATH', synthetic_quote_template)


@pytest.fixture(autouse=True)
def offline_dynamic_agent(monkeypatch, request):
    """离线测试没有 Docker/子代理：用代码发现结果顶替 agent.parse_dynamic。

    删A 后商品阶段解析只有子代理一条路（子代理失败即 ValueError，无代码回落）；
    导入流的既有用例靠这个替身拿到确定性的“子代理产出”（按标签把源行列值
    对号入座到模板字段，与真实子代理的输出形状一致）。需要验证“子代理挂了”
    的用例在测试体内自行 monkeypatch catalog.agent.parse_dynamic 覆盖本替身；
    验证容器链路本身（假 claude 可执行/Docker 隔离）的用例打 real_agent 标记，
    本夹具不替换，直连真实 parse_dynamic。
    """
    if request.node.get_closest_marker('real_agent'):
        return
    from catalog import agent, workbook_templates

    def _label_key(value):
        return ''.join(str(value or '').split()).casefold()

    def _fake_parse_dynamic(template, xlsx_path, work_dir, *, sheet=''):
        drafts = workbook_templates.discover_workbook(xlsx_path, work_dir)
        found = None
        if sheet:
            found = next((d for d in drafts
                          if sheet in (d.get('title'), d.get('source_sheet'), d.get('name'))), None)
            if found is None:
                return {'vendor': None, 'products': []}
        elif drafts:
            found = drafts[0]
        if found is None:
            return {'vendor': None, 'products': []}
        by_label = {}
        for field in found['fields']:
            by_label.setdefault(_label_key(field['label']), field)
        products = []
        for row in found['rows']:
            item = {}
            for field in template['fields']:
                if field.get('role') == 'image':
                    continue
                source = by_label.get(_label_key(field['label']))
                item[field['key']] = row['data'].get(source['key'], '') if source else ''
            images = [name for name in (row.get('images') or []) if name]
            item['image_main'] = row.get('image_main') or (images[0] if images else '')
            item['images'] = images
            products.append(item)
        return {'vendor': None, 'products': products}

    monkeypatch.setattr(agent, 'parse_dynamic', _fake_parse_dynamic)
