import json
import stat
import textwrap

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


def test_build_prompt_contains_template_fields_and_rules():
    p = agent.build_prompt('razor', '/tmp/in.xlsx', '/tmp/out.json')
    for label in ('产品型号', '彩盒尺寸mm', '报价'):
        assert label in p
    assert '每一行数据' in p and '不做任何合并' in p


def test_parse_returns_products_via_fake_agent(tmp_path, monkeypatch):
    out = str(tmp_path / 'products.json')
    (tmp_path/'in.xlsx').write_bytes(b'test workbook')
    monkeypatch.setenv('CATALOG_AGENT_CONTAINER_IMAGE','test-parser:fixture')
    monkeypatch.setattr(agent.shutil, 'which',
                        lambda _: _make_fake(tmp_path, out,
                                             [{'model_no': '8225', 'price': '21.5'}]))
    r = agent.parse('razor', str(tmp_path / 'in.xlsx'), str(tmp_path))
    assert r['vendor'] == '测试厂'
    assert r['products'][0]['model_no'] == '8225'


