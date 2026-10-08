"""agent 商品解析输出键抄短时的前缀回映射（2026-10-09 实发：glm-5.3 把
field_0f4371a7a9 写成 field_0f4371a7，价格整列被静默丢弃）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import agent, ai_extract, dynamic_import, fast_import, workbook_templates


def _template():
    return {'name': '测试分类', 'fields': [
        {'key': 'model', 'label': '型号', 'role': 'model', 'type': 'text', 'visibility': 'public'},
        {'key': 'field_0f4371a7a9', 'label': '价格', 'role': 'price', 'type': 'money', 'visibility': 'internal'},
        {'key': 'field_6aba0d030b', 'label': '起订量', 'role': 'spec', 'type': 'text', 'visibility': 'public'},
    ]}


def _patch_chain(monkeypatch, products):
    monkeypatch.setattr(workbook_templates, 'discover_workbook', lambda p, include_images=False: [
        {'source_sheet': 'Sheet1', 'rows': [{'source_row': 3}], 'observed_rows': [3], 'source_max_row': 5}])
    monkeypatch.setattr(fast_import, 'parse_structured', lambda *a, **k: None)
    monkeypatch.setattr(ai_extract, 'map_approved_fields', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'parse_dynamic', lambda t, p, w, sheet='': {
        'vendor': None, 'failures': [], 'products': products})


def test_truncated_field_key_maps_to_unique_prefix(tmp_path, monkeypatch):
    xlsx = tmp_path / 'source.xlsx'
    xlsx.write_bytes(b'PK-dummy')
    _patch_chain(monkeypatch, [{
        'source_sheet': 'Sheet1', 'source_rows': [3], 'model': 'KZ-1',
        'field_0f4371a7': '28.5元/台（不含税运）', 'field_6aba0d030b': '1000',
        'images': [], 'image_count': 0}])
    rows = dynamic_import._agent_rows(_template(), str(xlsx), tmp_path / 'work', sheet='Sheet1')
    assert rows and rows[0]['data']['field_0f4371a7a9'] == '28.5元/台（不含税运）'


def test_unmappable_unknown_key_dropped_with_warning(tmp_path, monkeypatch, capsys):
    xlsx = tmp_path / 'source.xlsx'
    xlsx.write_bytes(b'PK-dummy')
    _patch_chain(monkeypatch, [{
        'source_sheet': 'Sheet1', 'source_rows': [3], 'model': 'KZ-1',
        'something_unrelated': '值', 'field_6aba0d030b': '1000',
        'images': [], 'image_count': 0}])
    rows = dynamic_import._agent_rows(_template(), str(xlsx), tmp_path / 'work', sheet='Sheet1')
    assert rows and 'something_unrelated' not in rows[0]['data']
    assert rows[0]['data']['field_0f4371a7a9'] == ''
    assert 'something_unrelated' in capsys.readouterr().out
