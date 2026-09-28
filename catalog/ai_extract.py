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


_DISCOVER_PROMPT = """你是 Excel 表头结构分析员。给你工作表的证据：前几行单元格网格（空串=空格）、每列嵌入图片锚点数、合并单元格区。
任务：找出每一列的真实表头字段。规则：
1. 表头可能多级/跨行合并：合并值在左上角，语义覆盖整列；把网格里能对应到列的表头文字都归到对应列；
2. 只有两种情况可以判 role=image：a) 该列网格里没有表头文字、但"图片锚点列"显示该列有图片（label 给"图片"）；b) 表头文字本身含 图片/照片/photo/image 字样。其他有文字表头的列一律不许判 image；
3. 无表头又无图片锚点的列跳过；不发明列；
4. role 白名单：model(该表唯一型号/货号列)/image/sequence(纯序号)/price(价格类)/cost(成本)/stock(库存)/note(备注链接)/spec(其他规格)；
5. type 按内容定：图片列=image、价格列=money、纯数字量词列=number、其余 text；visibility：价格/成本/库存/供应商类 internal 其余 public；searchable 只给型号/品名列 true。
对每个工作表输出一个对象，输出 JSON 数组（只输出 JSON）：
[{"title":"Sheet名","header_row":2,"columns":[{"col":1,"label":"...","role":"model","type":"text","visibility":"public","searchable":true}]}]
证据："""


def discover_headers(sheets_evidence: list) -> list | None:
    """模板表头发现（B 案）：代码证据 + qwen3.8-max 关思考直调，5-8 秒/表。

    失败返回 None，调用方回落 Docker 子代理（慢而稳）。
    """
    if not _enabled() or not sheets_evidence:
        return None
    try:
        raw = llm.chat_text(
            '你是 Excel 表头结构分析员，只输出 JSON。',
            [{'role': 'user',
              'content': _DISCOVER_PROMPT + json.dumps(sheets_evidence, ensure_ascii=False)}],
            temperature=0.1, extra={'enable_thinking': False, 'max_tokens': 3000})
        data = _parse_json_array(raw)
        return data if isinstance(data, list) and data else None
    except Exception as exc:  # noqa: BLE001
        print(f'[ai_extract] qwen 表头发现失败：{exc}', flush=True)
        return None


def map_approved_fields(path: str, fields: list[dict], sheet: str) -> dict | None:
    """Map approved fields to source columns using a small header/sample probe.

    This returns column numbers only. Product values and image bytes always come
    from the workbook, and an incomplete/ambiguous map uses the agent instead.
    """
    if not _enabled() or not fields:
        return None
    try:
        from . import workbook_templates
        evidence = [item for item in workbook_templates.extract_header_evidence(path, max_rows=16)
                    if not sheet or item['title'] == sheet]
        if len(evidence) != 1:
            return None
        labels = [f['label'] for f in fields]
        if len(set(labels)) != len(labels):
            return None
        prompt = ('给定已审批模板字段和 Excel 表头及前16行样本，按原始列号一一对应。'
                  '字段可能来自无表头的值列；不能确定就填 null，不要把无关数字杂项列强行映射。'
                  '只输出 JSON 对象 {"header_row":数据开始前最后一行,'
                  '"columns":{"字段名":列号或null}}。证据：'
                  + json.dumps({'fields': labels, 'sheets': evidence}, ensure_ascii=False))
        raw = llm.chat_text('你是 Excel 列对应分析员，只输出 JSON。',
                            [{'role': 'user', 'content': prompt}], temperature=0.1,
                            extra={'enable_thinking': False, 'max_tokens': 1600})
        answer = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()))
        header_row = answer.get('header_row')
        proposed = answer.get('columns')
        if type(header_row) is not int or not 1 <= header_row <= 30 or not isinstance(proposed, dict):
            return None
        mapped = {}
        for field in fields:
            col = proposed.get(field['label'])
            if type(col) is int and 1 <= col <= 200:
                mapped[field['key']] = col
        # The approved image field may have no printed header. Use the sole
        # blank image-anchor column only when it cannot collide with another.
        image_anchors = evidence[0]['图片锚点列']
        for field in fields:
            if field['role'] != 'image' or field['key'] in mapped:
                continue
            candidates = [int(col) for col in image_anchors
                          if int(col) not in mapped.values()
                          and all(int(col) > len(row) or not row[int(col)-1].strip()
                                  for row in evidence[0]['grid'][:header_row])]
            if len(candidates) == 1:
                mapped[field['key']] = candidates[0]
        if len(mapped) != len(fields) or len(set(mapped.values())) != len(mapped):
            return None
        return {'header_row': header_row, 'columns': mapped}
    except Exception as exc:  # noqa: BLE001
        print(f'[ai_extract] 已审批字段列映射失败：{type(exc).__name__}', flush=True)
        return None
