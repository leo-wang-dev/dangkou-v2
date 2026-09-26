import json
import stat
import textwrap

import pytest

from catalog import agent


def _make_fake(tmp_path, out_json, products):
    fake = tmp_path / 'claude'
    body = textwrap.dedent(f'''
        #!/bin/bash
        mkdir -p "$(dirname "{out_json}")"
        cat > "{out_json}" << 'EOF'
        {{"vendor": "测试厂", "products": {json.dumps(products, ensure_ascii=False)}}}
        EOF
        echo '{{"type":"result","subtype":"success","result":"DONE 1"}}'
    ''').strip()
    fake.write_text(body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return str(fake)


def _template():
    return {'key': 'test_cat', 'name': '测试品类',
            'fields': [{'key': 'model', 'label': '型号', 'role': 'model'},
                       {'key': 'price', 'label': '价格', 'role': 'price'}]}


def test_dynamic_prompt_carries_template_fields_and_rules():
    p = agent.build_dynamic_prompt(_template(), '/tmp/in.xlsx', '/tmp/out.json', sheet='SheetA')
    assert '型号(model)' in p and '价格(price)' in p   # 字段 label(key) 成对注入
    assert '只处理工作表「SheetA」' in p                # 指定 sheet 时其他 Sheet 忽略
    assert '合并为一个商品' in p                        # 跨物理行按型号归并
    assert '禁止编造、改写、翻译、换算单位' in p
    assert 'DONE N' in p


@pytest.mark.real_agent
def test_parse_dynamic_returns_products_via_fake_agent(tmp_path, monkeypatch):
    out = str(tmp_path / 'products.json')
    (tmp_path / 'in.xlsx').write_bytes(b'test workbook')
    monkeypatch.setenv('CATALOG_AGENT_CONTAINER_IMAGE', 'test-parser:fixture')
    monkeypatch.setattr(agent.shutil, 'which',
                        lambda _: _make_fake(tmp_path, out,
                                             [{'model': '8225', 'price': '21.5'}]))
    r = agent.parse_dynamic(_template(), str(tmp_path / 'in.xlsx'), str(tmp_path))
    assert r['vendor'] == '测试厂'
    assert r['products'][0]['model'] == '8225'
