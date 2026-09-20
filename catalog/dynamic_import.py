"""Build and apply approval payloads for Sheet-defined category imports."""
from __future__ import annotations

import json
import hashlib
import secrets

from . import dynamic_catalog, inner_code, workbook_templates


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


def build_ticket_payload(conn, xlsx_path, work_dir, *, source_key: str, doc_id: int | None = None) -> dict:
    sheets = []
    for discovered in workbook_templates.discover_workbook(xlsx_path, work_dir):
        template = _clean_template(discovered)
        try:
            current = dynamic_catalog.get_template(conn, template['key'])
        except KeyError:
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
        sheets.append({'template': template, 'template_action': action,
                       'expected_version': expected, 'title': discovered['title'],
                       'header_row': discovered['header_row'], 'image_count': discovered['image_count'],
                       'source_snapshot': _source_snapshot(source_rows),
                       'drafts': _classify_rows(existing, discovered['rows'], model_keys)})
    if not sheets:
        raise ValueError('Excel 中没有识别到有效 Sheet 和表头')
    return {'kind': 'template_import', 'doc_id': doc_id, 'source_key': source_key,
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


def apply_ticket(conn, payload: dict, decisions: dict | None = None) -> dict:
    decisions = decisions or {}
    rejected = {str(value) for value in decisions.get('reject', [])}
    edits = decisions.get('edits') or {}
    template_edits = decisions.get('templates') or {}
    created = updated = delisted = 0
    created_rows = []
    source = payload.get('source_key', '')
    for section in payload['sheets']:
        template = section['template']
        sheet_name = template.get('source_sheet') or template['name']
        current_rows = []
        try:
            current_rows = _source_rows(conn, template['key'], source, sheet_name)
        except KeyError:
            pass
        if section.get('source_snapshot') != _source_snapshot(current_rows):
            from .tickets import TicketConflict
            raise TicketConflict('商品数据已变化，请重新导入并审批，避免覆盖最新修改')
    for section in payload['sheets']:
        template_override = template_edits.get(section['template']['key'])
        template = {**section['template'], **(template_override or {})}
        try:
            if section['template_action'] in {'create', 'update'}:
                approved = dynamic_catalog.approve_template(
                    conn, template, expected_version=section['expected_version'])
            else:
                approved = dynamic_catalog.get_template(conn, template['key'])
                if approved['version'] != section['expected_version']:
                    raise ValueError('分类模板已经变化，请重新审批')
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
        sheet_name = template.get('source_sheet') or template['name']
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
    return {'created': created, 'updated': updated, 'delisted': delisted,
            'created_rows': created_rows, 'work_dir': payload.get('work_dir')}
