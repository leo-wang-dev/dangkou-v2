"""Use an explicit synthetic template for offline tests, never install it in production data."""
import os
import pytest


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
def offline_dynamic_agent(monkeypatch):
    """离线测试没有 Docker/子代理：用代码发现结果顶替 agent.parse_dynamic。

    删A 后商品阶段解析只有子代理一条路（子代理失败即 ValueError，无代码回落）；
    导入流的既有用例靠这个替身拿到确定性的“子代理产出”（按标签把源行列值
    对号入座到模板字段，与真实子代理的输出形状一致）。需要验证“子代理挂了”
    的用例在测试体内自行 monkeypatch catalog.agent.parse_dynamic 覆盖本替身。
    """
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
