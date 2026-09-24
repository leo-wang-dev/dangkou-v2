"""Build and apply approval payloads for Sheet-defined category imports."""
from __future__ import annotations

import json
import hashlib
import os
import secrets

from . import agent, ai_extract, dynamic_catalog, inner_code, workbook_templates


def _clean_template(draft: dict) -> dict:
    return {'key': draft['key'], 'name': draft['name'],
            'source_sheet': draft['source_sheet'], 'storage': 'dynamic',
            'fields': [{key: value for key, value in field.items() if key != 'source_column'}
                       for field in draft['fields']]}


def _field_signature(fields: list[dict]) -> list[tuple]:
    return [(field['label'].casefold(), field['type'], field['visibility'], field['role'])
            for field in fields]


def _source_rows(conn, category_key: str, source_key: str, source_sheet: str) -> list[dict]:
    return [row for row in dynamic_catalog.list_products(conn, category_key)
            if row['source_key'] == source_key and row['source_sheet'] == source_sheet
            ]


def _source_snapshot(rows: list[dict]) -> str:
    values = [{key: row.get(key) for key in (
        'id', 'inner_code', 'data', 'status', 'images', 'source_row',
        'row_fingerprint', 'cs_visible')} for row in sorted(rows, key=lambda value: value['id'])]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _model_identity(row: dict, model_keys: list[str]):
    data = row.get('data') or {}
    values = tuple(str(data.get(key) or '').strip().casefold() for key in model_keys)
    return values if any(values) else None


def _classify_rows(existing: list[dict], incoming: list[dict], model_keys: list[str]) -> dict:
    unused = list(existing)
    new, update = [], []
    matches: dict[int, dict] = {}
    # Reserve every exact content match first. This disambiguates duplicate
    # models when just one colour/spec row changed.
    for index, draft in enumerate(incoming):
        match = next((row for row in unused if row['row_fingerprint'] == draft['row_fingerprint']), None)
        if match is not None:
            unused.remove(match)
            matches[index] = match
    for index, draft in enumerate(incoming):
        match = matches.get(index)
        if match is None:
            identity = _model_identity(draft, model_keys)
            candidates = [row for row in unused if identity and _model_identity(row, model_keys) == identity]
            match = candidates[0] if len(candidates) == 1 else None
        if match is None:
            new.append(draft)
            continue
        if match in unused:
            unused.remove(match)
        if match['row_fingerprint'] != draft['row_fingerprint']:
            update.append([match, draft])
    return {'new': new, 'update': update, 'delist': unused}


def _label_key(value: str) -> str:
    return ''.join(str(value or '').split()).casefold()


def _file_sha256(path: str) -> str:
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1048576), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ''


def _agent_rows(template: dict, xlsx_path: str, work_dir, sheet: str = '') -> list[dict] | None:
    """子代理（Claude）整表语义解析：合并跨行商品、图片按锚点归属、容忍乱表。

    Docker/代理不可用或产出为空时返回 None，调用方回落代码行切分——导入不硬失败。
    """
    try:
        result = agent.parse_dynamic(template, xlsx_path, work_dir, sheet=sheet)
    except Exception as exc:  # noqa: BLE001
        print(f'[dynamic_import] 子代理解析失败，回落代码解析：{exc}', flush=True)
        return None
    rows = []
    for p in result.get('products') or []:
        if not isinstance(p, dict):
            continue
        data = {}
        for f in template['fields']:
            if f.get('role') == 'image':
                continue
            value = p.get(f['key'], p.get(f['label']))
            data[f['key']] = '' if value is None else str(value)
        if not any(str(v).strip() for v in data.values()):
            continue
        images = [str(v) for v in (p.get('images') or []) if v]
        if p.get('image_main') and p['image_main'] not in images:
            images.insert(0, str(p['image_main']))
        fingerprint_payload = {'data': data,
                               'images': [_file_sha256(os.path.join(str(work_dir), name)) for name in images]}
        rows.append({'data': data, 'images': images,
                     'image_main': images[0] if images else '',
                     'source_row': None,
                     'row_fingerprint': hashlib.sha256(json.dumps(
                         fingerprint_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()})
    if not rows:
        print('[dynamic_import] 子代理产出 0 条商品，回落代码解析', flush=True)
        return None
    print(f'[dynamic_import] 子代理解析产出 {len(rows)} 条商品（sheet={sheet or "全部"}）', flush=True)
    return rows


def _map_rows(found: dict, template: dict) -> list[dict]:
    """商品行对号入座：AI 语义映射为主（容忍列名对不上、规格跨列），

    模型故障或明确禁用时回落代码标签精确匹配，导入不硬失败。
    """
    try:
        mapped = ai_extract.map_rows(found, template)
        if mapped is not None:
            return mapped
    except Exception:
        pass
    return _map_rows_to_template(found, template)


def manual_template_payload(name: str, fields_in: list[dict]) -> dict:
    """手工建分类（不经 Excel）：字段 key 生成与 Excel 导入同源，身份稳定。"""
    used: set[str] = set()
    fields = []
    for item in fields_in:
        role, field_type, visibility, searchable = workbook_templates._role(item['label'])
        fields.append({'key': workbook_templates._field_key(item['label'], role, used),
                       'label': item['label'], 'type': field_type, 'required': False,
                       'visibility': item.get('visibility') or visibility,
                       'searchable': searchable, 'role': role})
    key = workbook_templates._category_key(name)
    template = {'key': key, 'name': name, 'source_sheet': name,
                'storage': 'dynamic', 'fields': fields}
    return {'kind': 'template_import', 'phase': 'template', 'manual': True,
            'doc_id': None, 'source_key': f'manual:{key}', 'mode': 'new',
            'category_key': None, 'work_dir': '',
            'sheets': [{'template': template, 'template_action': 'create',
                        'expected_version': 0, 'title': name, 'header_row': None,
                        'image_count': 0, 'source_sheet': name,
                        'source_discovered_sheet': '', 'source_snapshot': '',
                        'drafts': {'new': [], 'update': [], 'delist': []}}]}


def _map_rows_to_template(discovered: dict, template: dict) -> list[dict]:
    """Map a workbook sheet onto an existing template without changing its schema."""
    by_label = {_label_key(field['label']): field for field in template['fields']}
    note_fields = [field for field in template['fields'] if field['role'] == 'note']
    mapped = []
    for row in discovered['rows']:
        data = {}
        extras = []
        for source_field in discovered['fields']:
            target = by_label.get(_label_key(source_field['label']))
            if target is None:
                value = str(row.get('data', {}).get(source_field['key'], '') or '').strip()
                if value:
                    extras.append(f'{source_field["label"]}：{value}')
                continue
            if target['role'] != 'image':
                data[target['key']] = row.get('data', {}).get(source_field['key'], '')
        if extras and note_fields:
            note_key = note_fields[0]['key']
            data[note_key] = '｜'.join(extras)
        mapped.append({**row, 'data': data})
    return mapped


def _pending_claim_conflict(conn, key: str, signature) -> bool:
    """同名 Sheet 的在途工单已认领该分类 key 且表头不同——
    同 key 不同表头无法合并，后到的文件必须开独立分类，避免生成注定批不过的工单。
    表头相同的在途认领不算冲突：两张工单先后批准会按增量并入同一分类。"""
    rows = conn.execute("SELECT payload FROM approval_ticket "
                        "WHERE status='pending' AND ticket_type='template_import'").fetchall()
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError):
            continue
        for section in payload.get('sheets') or []:
            if section.get('template', {}).get('key') != key:
                continue
            if _field_signature(section['template']['fields']) != signature:
                return True
    return False


def _unique_new_template(conn, template: dict, *, source_key: str, doc_id: int | None) -> dict:
    """Force mode=new to create a distinct category even when the sheet name exists."""
    candidate = dict(template)
    base_key = template['key']
    base_name = template['name']
    signature = _field_signature(template['fields'])
    suffix = 0
    while True:
        try:
            current = dynamic_catalog.get_template(conn, candidate['key'])
        except KeyError:
            current = None
        if current is None and not _pending_claim_conflict(conn, candidate['key'], signature):
            return candidate
        suffix += 1
        seed = f'{base_key}\0new\0{source_key}\0{doc_id or ""}\0{suffix}'
        candidate['key'] = 'cat_' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]
        candidate['name'] = f'{base_name} ({suffix})'


def _template_sections(conn, discovered_sheets: list[dict], *, source_key: str,
                       doc_id: int | None, mode: str | None,
                       category_key: str | None) -> list[dict]:
    """Build schema-only sections.  No product rows or embedded images are attached."""
    if mode == 'existing':
        try:
            target = dynamic_catalog.get_template(conn, category_key)
        except KeyError as exc:
            raise ValueError('未知目标分类，请先从商品管理中选择已有分类') from exc
        if target['storage'] != 'dynamic':
            raise ValueError(f'分类 {target["name"]} 是预置分类，请继续使用原品类导入入口')
        discovered = discovered_sheets[0] if discovered_sheets else {}
        return [{'template': target, 'template_action': 'reuse',
                 'expected_version': target['version'],
                 'title': discovered.get('title', ''),
                 'header_row': discovered.get('header_row'), 'image_count': 0,
                 'source_sheet': target['source_sheet'], 'source_discovered_sheet': discovered.get('source_sheet', ''),
                 'source_snapshot': '',
                 'drafts': {'new': [], 'update': [], 'delist': []}}]

    sections = []
    for discovered in discovered_sheets:
        template = _clean_template(discovered)
        if mode == 'new':
            template = _unique_new_template(conn, template, source_key=source_key, doc_id=doc_id)
        try:
            current = dynamic_catalog.get_template(conn, template['key'])
        except KeyError:
            current = None
        if mode == 'new':
            current = None
        if current is None:
            action, expected = 'create', 0
        else:
            if current['storage'] != 'dynamic':
                raise ValueError(f'分类 {current["name"]} 是预置分类，请继续使用原品类导入入口')
            expected = current['version']
            action = 'reuse' if _field_signature(current['fields']) == _field_signature(template['fields']) else 'update'
        sections.append({'template': template, 'template_action': action,
                         'expected_version': expected, 'title': discovered['title'],
                         'header_row': discovered['header_row'], 'image_count': 0,
                         'source_sheet': discovered['source_sheet'],
                         'source_snapshot': '',
                         'drafts': {'new': [], 'update': [], 'delist': []}})
    return sections


def build_template_payload(conn, xlsx_path, work_dir, *, source_key: str,
                           doc_id: int | None = None, mode: str | None = None,
                           category_key: str | None = None) -> dict:
    """Create a small template-only approval payload.

    The workbook is opened once for schema discovery, but rows and images are
    intentionally skipped.  Product extraction happens only after this ticket
    is approved and the merchant uploads the workbook again.
    """
    if mode not in (None, 'new', 'existing'):
        raise ValueError('导入 mode 只能是 new 或 existing')
    if mode == 'existing' and not category_key:
        raise ValueError('并入已有分类时必须提供 category_key')
    if mode != 'existing' and category_key:
        raise ValueError('只有 existing 模式可以提供 category_key')
    discovered = workbook_templates.discover_workbook(
        xlsx_path, None, include_rows=False, include_images=False)
    # 模板阶段字段属性（类型/角色/可见性）由 AI 推断，代码推断保留为回落。
    ai_extract.apply_field_attributes(discovered, ai_extract.infer_field_attributes(discovered))
    sections = _template_sections(conn, discovered, source_key=source_key,
                                  doc_id=doc_id, mode=mode, category_key=category_key)
    if not sections:
        raise ValueError('Excel 中没有识别到有效 Sheet 和表头')
    return {'kind': 'template_import', 'phase': 'template', 'doc_id': doc_id,
            'source_key': source_key, 'mode': mode, 'category_key': category_key,
            'work_dir': str(work_dir), 'sheets': sections}


def _template_session(conn, template_doc_id: int) -> tuple[dict, list[dict]]:
    row = conn.execute('SELECT * FROM import_doc WHERE id=?', (template_doc_id,)).fetchone()
    if row is None or row['phase'] != 'template' or row['status'] != 'template_approved':
        raise ValueError('模板工单尚未审批通过，请先审批模板后再上传商品 Excel')
    try:
        mapping = json.loads(row['template_keys_json'] or '[]')
    except (TypeError, ValueError) as exc:
        raise ValueError('模板工单缺少有效的分类映射，请重新导入模板') from exc
    if not isinstance(mapping, list) or not mapping:
        raise ValueError('模板工单没有可用分类，请重新导入模板')
    return dict(row), mapping


def build_product_payload(conn, xlsx_path, work_dir, *, source_key: str,
                          template_doc_id: int, doc_id: int | None = None,
                          mode: str | None = None,
                          category_key: str | None = None) -> dict:
    """Parse a second upload against an already approved template session."""
    session, mapping = _template_session(conn, template_doc_id)
    # SQLite stores omitted mode as an empty string.  Treat that as the same
    # value as an omitted API argument so legacy callers can complete the
    # second phase without being rejected for a representation difference.
    if (mode or '') != (session.get('mode') or ''):
        raise ValueError('商品导入方式必须与模板导入一致，请使用模板工单返回的 mode')
    if (category_key or '') != (session.get('category_key') or ''):
        raise ValueError('商品导入目标分类必须与模板工单一致')
    discovered = workbook_templates.discover_workbook(xlsx_path, work_dir)
    sections = []
    if mode == 'existing':
        # The existing-category contract accepts a workbook with several
        # sheets.  The template phase records one approved target schema;
        # the product phase maps every re-uploaded sheet into that schema so
        # no sheet silently disappears during the split.
        item = mapping[0]
        key = item.get('category_key') or category_key
        try:
            template = dynamic_catalog.get_template(conn, key)
        except KeyError as exc:
            raise ValueError('模板对应的分类已不存在，请重新导入模板') from exc
        expected = int(item.get('version') or 0)
        if template['version'] != expected:
            raise ValueError('分类模板已经更新，请重新导入并审批模板后再上传商品 Excel')
        if not discovered:
            raise ValueError('第二次上传没有识别到有效 Sheet，请上传同一份商品 Excel')
        incoming = _agent_rows(template, xlsx_path, work_dir)
        if incoming is None:
            incoming = []
            for found in discovered:
                incoming.extend(_map_rows(found, template))
        source_rows = _source_rows(conn, template['key'], source_key, template['source_sheet'])
        existing = [row for row in source_rows if row['status'] != 'delisted']
        model_keys = [field['key'] for field in template['fields'] if field['role'] == 'model']
        sections.append({'template': template, 'template_action': 'reuse',
                         'expected_version': template['version'],
                         'title': discovered[0]['title'],
                         'header_row': discovered[0]['header_row'],
                         'image_count': sum(item['image_count'] for item in discovered),
                         'source_sheet': template['source_sheet'],
                         'source_snapshot': _source_snapshot(source_rows),
                         'drafts': _classify_rows(existing, incoming, model_keys)})
        return {'kind': 'template_import', 'phase': 'products', 'doc_id': doc_id,
                'template_doc_id': template_doc_id, 'source_key': source_key,
                'mode': mode, 'category_key': category_key, 'work_dir': str(work_dir),
                'sheets': sections}
    by_sheet = {item['source_sheet']: item for item in discovered}
    for item in mapping:
        source_sheet = item.get('source_sheet') or ''
        found = by_sheet.get(source_sheet)
        if found is None and mode == 'existing' and discovered:
            found = discovered[0]
        if found is None:
            raise ValueError(f'第二次上传缺少模板 Sheet“{source_sheet}”，请上传同一份商品 Excel')
        key = item.get('category_key') or category_key
        try:
            template = dynamic_catalog.get_template(conn, key)
        except KeyError as exc:
            raise ValueError('模板对应的分类已不存在，请重新导入模板') from exc
        expected = int(item.get('version') or 0)
        if template['version'] != expected:
            raise ValueError('分类模板已经更新，请重新导入并审批模板后再上传商品 Excel')
        incoming = (_agent_rows(template, xlsx_path, work_dir, sheet=found.get('title') or '')
                    or _map_rows(found, template))
        source_rows = _source_rows(conn, template['key'], source_key, template['source_sheet'])
        existing = [row for row in source_rows if row['status'] != 'delisted']
        model_keys = [field['key'] for field in template['fields'] if field['role'] == 'model']
        sections.append({'template': template, 'template_action': 'reuse',
                         'expected_version': template['version'], 'title': found['title'],
                         'header_row': found['header_row'], 'image_count': found['image_count'],
                         'source_sheet': template['source_sheet'],
                         'source_snapshot': _source_snapshot(source_rows),
                         'drafts': _classify_rows(existing, incoming, model_keys)})
    return {'kind': 'template_import', 'phase': 'products', 'doc_id': doc_id,
            'template_doc_id': template_doc_id, 'source_key': source_key,
            'mode': mode, 'category_key': category_key, 'work_dir': str(work_dir),
            'sheets': sections}


def build_ticket_payload(conn, xlsx_path, work_dir, *, source_key: str,
                         doc_id: int | None = None, mode: str | None = None,
                         category_key: str | None = None) -> dict:
    if mode not in (None, 'new', 'existing'):
        raise ValueError('导入 mode 只能是 new 或 existing')
    if mode == 'existing' and not category_key:
        raise ValueError('并入已有分类时必须提供 category_key')
    if mode != 'existing' and category_key:
        raise ValueError('只有 existing 模式可以提供 category_key')

    discovered_sheets = workbook_templates.discover_workbook(xlsx_path, work_dir)
    if mode == 'existing':
        try:
            target = dynamic_catalog.get_template(conn, category_key)
        except KeyError as exc:
            raise ValueError('未知目标分类，请先从商品管理中选择已有分类') from exc
        if target['storage'] != 'dynamic':
            raise ValueError(f'分类 {target["name"]} 是预置分类，请继续使用原品类导入入口')
        incoming = []
        for discovered in discovered_sheets:
            incoming = _agent_rows(target, xlsx_path, work_dir, sheet=discovered.get('title') or '')
            if incoming is None:
                incoming = _map_rows(discovered, target)
            else:
                incoming = list(incoming)
        source_rows = _source_rows(conn, target['key'], source_key, target['source_sheet'])
        existing = [row for row in source_rows if row['status'] != 'delisted']
        model_keys = [field['key'] for field in target['fields'] if field['role'] == 'model']
        section = {'template': target, 'template_action': 'reuse',
                   'expected_version': target['version'], 'title': '',
                   'header_row': None, 'image_count': sum(s['image_count'] for s in discovered_sheets),
                   'source_sheet': target['source_sheet'],
                   'source_snapshot': _source_snapshot(source_rows),
                   'drafts': _classify_rows(existing, incoming, model_keys)}
        if discovered_sheets:
            section['title'] = discovered_sheets[0]['title']
            section['header_row'] = discovered_sheets[0]['header_row']
        return {'kind': 'template_import', 'doc_id': doc_id, 'source_key': source_key,
                'mode': mode, 'category_key': category_key, 'work_dir': str(work_dir),
                'sheets': [section]}

    sheets = []
    for discovered in discovered_sheets:
        template = _clean_template(discovered)
        if mode == 'new':
            template = _unique_new_template(conn, template, source_key=source_key, doc_id=doc_id)
        try:
            current = dynamic_catalog.get_template(conn, template['key'])
        except KeyError:
            current = None
        if mode == 'new':
            current = None
        if current is None:
            action, expected, existing = 'create', 0, []
            source_rows = []
        else:
            if current['storage'] != 'dynamic':
                raise ValueError(f'分类 {current["name"]} 是预置分类，请继续使用原品类导入入口')
            expected = current['version']
            action = 'reuse' if _field_signature(current['fields']) == _field_signature(template['fields']) else 'update'
            source_rows = _source_rows(conn, current['key'], source_key, discovered['source_sheet'])
            existing = [row for row in source_rows if row['status'] != 'delisted']
        model_keys = [field['key'] for field in template['fields'] if field['role'] == 'model']
        incoming = (_agent_rows(template, xlsx_path, work_dir, sheet=discovered.get('title') or '')
                    or list(discovered['rows']))
        sheets.append({'template': template, 'template_action': action,
                       'expected_version': expected, 'title': discovered['title'],
                       'header_row': discovered['header_row'], 'image_count': discovered['image_count'],
                       'source_sheet': discovered['source_sheet'],
                       'source_snapshot': _source_snapshot(source_rows),
                       'drafts': _classify_rows(existing, incoming, model_keys)})
    if not sheets:
        raise ValueError('Excel 中没有识别到有效 Sheet 和表头')
    return {'kind': 'template_import', 'doc_id': doc_id, 'source_key': source_key,
            'mode': mode, 'category_key': category_key,
            'work_dir': str(work_dir), 'sheets': sheets}


def _edited(draft: dict, edits: dict, *aliases) -> dict:
    keys = [draft.get('_rid'), *aliases]
    change = next((edits.get(key) or edits.get(str(key)) for key in keys
                   if key is not None and (edits.get(key) or edits.get(str(key)))), {})
    if not isinstance(change, dict):
        return draft
    result = {**draft}
    data_changes = change.get('data') if isinstance(change.get('data'), dict) else {
        key: value for key, value in change.items() if not key.startswith('__')}
    result['data'] = {**draft.get('data', {}), **data_changes}
    if isinstance(change.get('__images'), list):
        result['images'] = change['__images']
        result['image_main'] = change['__images'][0] if change['__images'] else ''
    return result


def _apply_template_ticket(conn, payload: dict, decisions: dict | None = None) -> dict:
    decisions = decisions or {}
    template_edits = decisions.get('templates') or {}
    mapping = []
    for section in payload.get('sheets') or []:
        template_override = template_edits.get(section['template']['key'])
        template = {**section['template'], **(template_override or {})}
        try:
            current = None
            try:
                current = dynamic_catalog.get_template(conn, template['key'])
            except KeyError:
                pass
            if section['template_action'] in {'create', 'update'}:
                approved = dynamic_catalog.approve_template(
                    conn, template, expected_version=section['expected_version'])
            else:
                if current is None or current['version'] != section['expected_version']:
                    raise ValueError('此工单已过期（分类模板已更新），请直接驳回；如需入库请重新发送 Excel')
                changed = template_override and (
                    current['name'] != template['name']
                    or current.get('source_sheet', '') != template.get('source_sheet', '')
                    or _field_signature(current['fields']) != _field_signature(template['fields']))
                approved = dynamic_catalog.approve_template(
                    conn, template, expected_version=section['expected_version']) if changed else current
        except ValueError as exc:
            from .tickets import TicketConflict
            raise TicketConflict(str(exc)) from exc
        mapping.append({'source_sheet': section.get('source_discovered_sheet') or section.get('source_sheet'),
                        'category_key': approved['key'], 'version': approved['version']})
    doc_id = payload.get('doc_id')
    if doc_id:
        conn.execute("UPDATE import_doc SET status='template_approved', template_keys_json=?, stats_json=? WHERE id=?",
                     (json.dumps(mapping, ensure_ascii=False),
                      json.dumps({'phase': 'template', 'template_doc_id': doc_id,
                                  'categories': [item['category_key'] for item in mapping],
                                  'next_action': 'upload_products'}, ensure_ascii=False), doc_id))
    return {'phase': 'template', 'template_approved': len(mapping),
            'template_doc_id': doc_id, 'template_keys': mapping,
            'next_action': 'upload_products'}


def _apply_product_payload(conn, payload: dict, decisions: dict | None = None) -> dict:
    return _apply_ticket_payload(conn, payload, decisions, product_only=True)


def _apply_ticket_payload(conn, payload: dict, decisions: dict | None = None,
                          *, product_only: bool = False) -> dict:
    decisions = decisions or {}
    rejected = {str(value) for value in decisions.get('reject', [])}
    edits = decisions.get('edits') or {}
    template_edits = decisions.get('templates') or {}
    created = updated = delisted = 0
    created_rows = []
    source = payload.get('source_key', '')
    for section in payload['sheets']:
        template = section['template']
        sheet_name = section.get('source_sheet') or template.get('source_sheet') or template['name']
        current_rows = []
        try:
            current_rows = _source_rows(conn, template['key'], source, sheet_name)
        except KeyError:
            pass
        if section.get('source_snapshot') != _source_snapshot(current_rows):
            from .tickets import TicketConflict
            raise TicketConflict('商品数据已变化，请重新导入并审批，避免覆盖最新修改')
    for section in payload['sheets']:
        template_override = {} if product_only else template_edits.get(section['template']['key'])
        template = {**section['template'], **(template_override or {})}
        try:
            current = None
            try:
                current = dynamic_catalog.get_template(conn, template['key'])
            except KeyError:
                pass
            if product_only:
                if current is None or current['version'] != section['expected_version']:
                    raise ValueError('分类模板已经更新，请重新导入模板并重新上传商品 Excel')
                approved = current
            elif (current is not None and not template_override
                    and current['version'] != section['expected_version']
                    and _field_signature(current['fields']) == _field_signature(template['fields'])):
                # 同名 Sheet 先后导入：模板已被同表头的其他工单创建/推进。
                # 表头一致即可安全按增量并入当前版本——商家要的“增量保留”，
                # 不再撞版本锁（真正表头变更的工单仍走下方过期报错）。
                approved = current
            elif section['template_action'] in {'create', 'update'}:
                approved = dynamic_catalog.approve_template(
                    conn, template, expected_version=section['expected_version'])
            else:
                approved = dynamic_catalog.get_template(conn, template['key'])
                if approved['version'] != section['expected_version']:
                    raise ValueError('此工单已过期（分类模板已更新），请直接驳回；如需入库请重新发送 Excel')
                changed = template_override and (
                    approved['name'] != template['name']
                    or approved.get('source_sheet', '') != template.get('source_sheet', '')
                    or _field_signature(approved['fields']) != _field_signature(template['fields']))
                if changed:
                    approved = dynamic_catalog.approve_template(
                        conn, template, expected_version=section['expected_version'])
        except ValueError as exc:
            from .tickets import TicketConflict
            raise TicketConflict(str(exc)) from exc
        allowed = {field['key'] for field in approved['fields']}
        sheet_name = section.get('source_sheet') or template.get('source_sheet') or template['name']
        for draft in section['drafts'].get('new', []):
            if str(draft.get('_rid')) in rejected:
                continue
            draft = _edited(draft, edits)
            product_id = secrets.token_hex(8)
            row = {'id': product_id, 'inner_code': inner_code.gen(), 'cs_visible': 1,
                   'data': {key: value for key, value in draft.get('data', {}).items() if key in allowed},
                   'images': draft.get('images') or [], 'source_row': draft.get('source_row'),
                   'row_fingerprint': draft.get('row_fingerprint', '')}
            outcome = dynamic_catalog.upsert_approved_products(
                conn, approved['key'], [row], source_key=source, source_sheet=sheet_name,
                source_doc=payload.get('doc_id'))
            created += outcome['created']
            created_rows.append({'id': product_id, '_category': approved['key'],
                                 '_table': 'product_dynamic', 'images': row['images'],
                                 'image_main': row['images'][0] if row['images'] else ''})
        for old, incoming in section['drafts'].get('update', []):
            if str(old['id']) in rejected or str(incoming.get('_rid')) in rejected:
                continue
            incoming = _edited(incoming, edits, old['id'])
            images = incoming['images'] if 'images' in incoming else (old.get('images') or [])
            row = {'id': old['id'], 'inner_code': old['inner_code'], 'cs_visible': old['cs_visible'],
                   'status': 'approved',
                   'data': {key: value for key, value in incoming.get('data', {}).items() if key in allowed},
                   'images': images,
                   'source_row': incoming.get('source_row'),
                   'row_fingerprint': incoming.get('row_fingerprint', '')}
            if 'images' in incoming:
                conn.execute('DELETE FROM embedding WHERE product_id=?', (old['id'],))
            outcome = dynamic_catalog.upsert_approved_products(
                conn, approved['key'], [row], source_key=source, source_sheet=sheet_name,
                source_doc=payload.get('doc_id'))
            updated += outcome['updated']
            if images:
                created_rows.append({'id': old['id'], '_category': approved['key'],
                                     '_table': 'product_dynamic', 'images': images,
                                     'image_main': images[0]})
        for old in section['drafts'].get('delist', []):
            if str(old['id']) in rejected:
                continue
            conn.execute("UPDATE product_dynamic SET status='delisted',updated_at=datetime('now') WHERE id=?",
                         (old['id'],))
            delisted += 1
    result = {'created': created, 'updated': updated, 'delisted': delisted,
              'created_rows': created_rows, 'work_dir': payload.get('work_dir')}
    if product_only:
        result.update({'phase': 'products', 'template_doc_id': payload.get('template_doc_id'),
                       'doc_id': payload.get('doc_id')})
        if payload.get('doc_id'):
            conn.execute("UPDATE import_doc SET status='product_approved', stats_json=? WHERE id=?",
                         (json.dumps({'phase': 'products', **{k: result[k] for k in ('created', 'updated', 'delisted')},
                                      'template_doc_id': payload.get('template_doc_id')}, ensure_ascii=False),
                          payload['doc_id']))
    return result


def apply_ticket(conn, payload: dict, decisions: dict | None = None) -> dict:
    phase = payload.get('phase')
    if phase == 'template':
        return _apply_template_ticket(conn, payload, decisions)
    if phase == 'products':
        return _apply_product_payload(conn, payload, decisions)
    return _apply_ticket_payload(conn, payload, decisions)
