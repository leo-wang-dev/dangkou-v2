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
    for label in ('产品型号', '彩盒尺寸(mm)', '报价'):
        assert label in p
    assert '同型号' in p and '绝不能出现两条' in p


def test_parse_returns_products_via_fake_agent(tmp_path, monkeypatch):
    out = str(tmp_path / 'products.json')
    monkeypatch.setattr(agent.shutil, 'which',
                        lambda _: _make_fake(tmp_path, out,
                                             [{'model_no': '8225', 'price': '21.5'}]))
    r = agent.parse('razor', str(tmp_path / 'in.xlsx'), str(tmp_path))
    assert r['vendor'] == '测试厂'
    assert r['products'][0]['model_no'] == '8225'


def test_parse_dedups_same_model(tmp_path, monkeypatch):
    out = str(tmp_path / 'products.json')
    monkeypatch.setattr(agent.shutil, 'which', lambda _: _make_fake(
        tmp_path, out,
        [{'model_no': '8225', 'price': '21.5'},
         {'model_no': '8225', 'price': '21.5', 'color': '黑'}]))
    r = agent.parse('curler' if False else 'razor', str(tmp_path / 'in.xlsx'), str(tmp_path))
    assert len(r['products']) == 1
    assert r['products'][0].get('color') == '黑'  # 保留字段更全的
