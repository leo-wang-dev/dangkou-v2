"""LLM 参与的 Excel 理解（动态分类导入）。

分工原则：结构（Sheet/表头/单元格值/图片锚点）由代码读取，保证值逐字忠实；
AI 只做模板阶段的语义判断——表头字段属性推断与供应商推断。
商品阶段解析只走子代理（见 catalog/agent.py），本模块不再参与。
AI 调用失败一律返回空结果，调用方回落代码推断，导入不因模型故障硬失败。
"""
import json
import os
import re

from . import config, llm

_ROLES = ('model', 'image', 'sequence', 'price', 'cost', 'stock', 'note', 'spec')


def _enabled() -> bool:
    return bool(config.BAILIAN_API_KEY) and os.environ.get('CATALOG_AI_IMPORT', '1') != '0'


def _parse_json_array(text: str) -> list:
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(text or '').strip())
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        raise ValueError('模型输出里找不到 JSON 数组')
    return json.loads(text[start:end + 1])


def infer_field_attributes(sheets: list[dict]) -> dict:
    """模板阶段：AI 推断每个表头字段的 type/role/visibility/searchable。

    输入 discover_workbook 的结果（fields 已带代码推断值），返回
    {sheet标题: {label: {type/role/visibility/searchable}}}；失败返回 {}。
    字段 key 与 label 不动——身份稳定，AI 只覆盖属性判断。
    """
    if not _enabled():
        return {}
    brief = []
    for sheet in sheets:
        headers = [f['label'] for f in sheet.get('fields', []) if f.get('label')]
        if headers:
            brief.append({'sheet': sheet.get('title') or sheet.get('name') or '',
                          'headers': headers})
    if not brief:
        return {}
    system = (
        '你是商品目录建模助手。对每个 Excel 表头字段判断属性，输出 JSON 数组，每项 '
        '{"sheet": 表名, "label": 表头原文, "type": "text|number|money|image", '
        '"role": "model|image|sequence|price|cost|stock|note|spec", '
        '"visibility": "public|internal", "searchable": true|false}。判断口径：'
        'role=model 是该表唯一的型号/货号/品名列（没有就给 spec）；role=image 是图片列；'
        'role=sequence 是纯序号列；role=price 是价格/报价/单价类列；role=cost 是成本/进货价列；'
        'role=stock 是库存列；role=note 是备注/说明/链接列；其余 spec。'
        'visibility 规则：价格、成本、库存、供应商类内部信息一律 internal（价格不对客户直接公开，'
        '正式报价走报价单流程），其余 public；searchable 只对客户会拿来搜索的字段（型号、品名）为 true。'
        '逐项覆盖输入里的每个表头，不发明不存在的表头。只输出 JSON。'
    )
    try:
        raw = llm.chat_text(
            system, [{'role': 'user', 'content': json.dumps(brief, ensure_ascii=False)}],
            temperature=0.1)
        out = {}
        for item in _parse_json_array(raw):
            if not isinstance(item, dict):
                continue
            sheet, label = str(item.get('sheet') or ''), str(item.get('label') or '')
            if not sheet or not label:
                continue
            attrs = {}
            if item.get('type') in ('text', 'number', 'money', 'image'):
                attrs['type'] = item['type']
            if item.get('role') in _ROLES:
                attrs['role'] = item['role']
            if item.get('visibility') in ('public', 'internal'):
                attrs['visibility'] = item['visibility']
            if isinstance(item.get('searchable'), bool):
                attrs['searchable'] = item['searchable']
            if attrs:
                out.setdefault(sheet, {})[label] = attrs
        return out
    except Exception:
        return {}


def apply_field_attributes(sheets: list[dict], attrs: dict) -> None:
    """把 AI 推断覆盖到 discovered sheets 的字段属性上（就地修改）。"""
    for sheet in sheets:
        by_label = attrs.get(sheet.get('title') or sheet.get('name') or '') or {}
        if not by_label:
            continue
        for field in sheet.get('fields', []):
            override = by_label.get(field.get('label'))
            if override:
                field.update(override)


def guess_supplier(source_key: str, sheet_names: list) -> str:
    """模板阶段推断供应商：文件名+Sheet 名交给 LLM 判断（不写关键词规则）。

    判断不出返回空串（调用方留空，页面/AI 随时可补）。
    """
    if not _enabled():
        return ''
    system = ('从商品 Excel 的文件名和工作表名里判断供应商/厂家名（中文优先，去掉"报价表/有限公司/'
              '有限公司报价单"等修饰，保留可读主体如"华岳电器"）。判断不出只输出空字符串。'
              '只输出供应商名本身，不要任何解释。')
    try:
        raw = llm.chat_text(system, [{'role': 'user', 'content': json.dumps(
            {'文件名': str(source_key or ''), '工作表': [str(n) for n in sheet_names]},
            ensure_ascii=False)}], temperature=0.1)
        return str(raw).strip().strip('"\'')[:40]
    except Exception:
        return ''
