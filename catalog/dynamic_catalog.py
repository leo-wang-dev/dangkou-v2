"""Merchant-scoped category templates and JSON-backed dynamic products."""
from __future__ import annotations

import json
import re
import secrets

from . import inner_code


FIELD_TYPES = {'text', 'number', 'money', 'image'}
VISIBILITIES = {'public', 'internal'}
FIELD_ROLES = {'model', 'spec', 'image', 'cost', 'price', 'stock', 'note', 'sequence'}
_KEY = re.compile(r'^[a-z][a-z0-9_]{0,63}$')


def _loads(value, fallback):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return result


def _field(value: dict) -> dict:
    key = str(value.get('key') or '').strip()
    label = str(value.get('label') or '').strip()
    if not _KEY.fullmatch(key):
        raise ValueError(f'字段 key 不合法：{key or "空"}')
    if not label:
        raise ValueError('字段名称不能为空')
    field_type = value.get('type', 'text')
    visibility = value.get('visibility', 'public')
    role = value.get('role', 'spec')
    if field_type not in FIELD_TYPES:
        raise ValueError(f'字段类型不支持：{field_type}')
    if visibility not in VISIBILITIES:
        raise ValueError(f'字段可见性不支持：{visibility}')
    if role not in FIELD_ROLES:
        raise ValueError(f'字段角色不支持：{role}')
    return {'key': key, 'label': label, 'type': field_type,
            'required': bool(value.get('required', False)),
            'visibility': visibility, 'searchable': bool(value.get('searchable', False)),
            'role': role}


def validate_template(draft: dict) -> dict:
    key = str(draft.get('key') or '').strip()
    name = str(draft.get('name') or '').strip()
    if not _KEY.fullmatch(key):
        raise ValueError('分类 key 只能使用小写字母、数字和下划线')
    if not name:
        raise ValueError('分类名称不能为空')
    fields = [_field(item) for item in draft.get('fields') or []]
    if not fields:
        raise ValueError('分类至少需要一个字段')
    keys = [item['key'] for item in fields]
    if len(keys) != len(set(keys)):
        raise ValueError('字段 key 不能重复')
    return {'key': key, 'name': name, 'fields': fields,
            'source_sheet': str(draft.get('source_sheet') or ''),
            'storage': str(draft.get('storage') or 'dynamic')}


def seed_legacy_templates(conn) -> None:
    from .templates import TEMPLATES
    for template in TEMPLATES.values():
        fields = []
        for col, label in template.fields:
            lowered = label.casefold()
            role = 'model' if col == template.dedup_field else ('cost' if '成本' in label else 'spec')
            visibility = 'internal' if role == 'cost' else 'public'
            fields.append({'key': col, 'label': label, 'type': 'money' if ('价格' in label or '报价' in label) else 'text',
                           'required': False, 'visibility': visibility,
                           'searchable': col == template.dedup_field or any(x in lowered for x in ('颜色', '型号')),
                           'role': role})
        snapshot = {'key': template.key, 'name': template.name, 'fields': fields,
                    'source_sheet': template.name, 'storage': 'legacy'}
        encoded = json.dumps(fields, ensure_ascii=False)
        conn.execute('INSERT OR IGNORE INTO category_template(key,name,version,fields_json,status,storage,source_sheet) '
                     "VALUES(?,?,1,?,'approved','legacy',?)",
                     (template.key, template.name, encoded, template.name))
        conn.execute('INSERT OR IGNORE INTO category_template_version(category_key,version,snapshot_json) VALUES(?,1,?)',
                     (template.key, json.dumps(snapshot, ensure_ascii=False)))


def _template(row) -> dict:
    return {'key': row['key'], 'name': row['name'],
            'supplier': _safe_col(row, 'supplier') or '', 'version': row['version'],
            'fields': _loads(row['fields_json'], []), 'status': row['status'],
            'storage': row['storage'], 'source_sheet': row['source_sheet'],
            'quote_map': _loads(_safe_col(row, 'quote_map_json'), {})}


def _safe_col(row, name):
    """极老库/迁移前没有该列时按空值处理。"""
    try:
        return row[name] or ''
    except (IndexError, KeyError):
        return ''


def list_templates(conn, *, approved_only: bool = True) -> list[dict]:
    where = " WHERE status='approved'" if approved_only else ''
    return [_template(row) for row in conn.execute(
        'SELECT * FROM category_template' + where + ' ORDER BY name,key')]


def get_template(conn, key: str) -> dict:
    row = conn.execute('SELECT * FROM category_template WHERE key=?', (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return _template(row)


def template_versions(conn, key: str) -> list[dict]:
    values = []
    for row in conn.execute('SELECT * FROM category_template_version WHERE category_key=? ORDER BY version', (key,)):
        snapshot = _loads(row['snapshot_json'], {})
        snapshot['version'] = row['version']
        values.append(snapshot)
    return values


QUOTE_MAP_FIELDS = ('model_field', 'price_field', 'ctn_field', 'color_field')


def suggest_quote_map(fields: list[dict]) -> dict:
    """从模板表头推断报价单列映射。价格/型号列唯一才自动绑；多候选或缺位返回缺项，
    由商家显式指定——价格口径不能猜。箱规/颜色按表头词匹配，命中唯一才绑。"""
    def by_role_unique(role):
        hits = [field for field in fields if field.get('role') == role]
        if hits:   # 角色已有候选：唯一才绑，多个直接拒绝（不降级到表头猜）
            return hits[0]['key'] if len(hits) == 1 else None
        return None

    def by_label_unique(*words):
        hits = [field for field in fields
                if any(word in field['label'].casefold() for word in words)]
        return hits[0]['key'] if len(hits) == 1 else None

    model = by_role_unique('model') or by_label_unique('型号', 'item.no', 'itemno', 'sku', '货号')
    price_role = [field for field in fields if field.get('role') == 'price']
    if price_role:
        price = by_role_unique('price')          # 歧义即拒绝，不落到表头猜
    else:
        price = by_label_unique('报价', '出厂价', '单价', '价格', 'price')
    mapping = {
        'model_field': model,
        'price_field': price,
        'ctn_field': by_label_unique('箱规', '装箱'),
        'color_field': by_label_unique('颜色', 'colour', 'color'),
    }
    return {name: key for name, key in mapping.items() if key}


def quotable(template: dict) -> bool:
    """报价单可出：型号列+价格列都已映射。"""
    quote_map = template.get('quote_map') or {}
    return bool(quote_map.get('model_field') and quote_map.get('price_field'))


def set_quote_map(conn, key: str, mapping: dict) -> dict:
    template = get_template(conn, key)
    if template['storage'] != 'dynamic':
        raise ValueError('预置分类的报价映射是固定配置，不能修改')
    by_key = {field['key']: field for field in template['fields']}
    by_label = {field['label'].casefold(): field for field in template['fields']}
    resolved = {}
    for name in QUOTE_MAP_FIELDS:
        value = str((mapping or {}).get(name) or '').strip()
        if not value:
            continue
        field = by_key.get(value) or by_label.get(value.casefold())
        if field is None:
            raise ValueError(f'字段「{value}」不在分类「{template["name"]}」的表头里')
        resolved[name] = field['key']
    if 'price_field' not in resolved:
        raise ValueError('报价映射必须指定价格列 price_field（可用字段名或字段 key）')
    if 'model_field' not in resolved:
        raise ValueError('报价映射必须指定型号列 model_field（可用字段名或字段 key）')
    merged = {**(template.get('quote_map') or {}), **resolved}
    conn.execute("UPDATE category_template SET quote_map_json=?, updated_at=datetime('now') WHERE key=?",
                 (json.dumps(merged, ensure_ascii=False), key))
    conn.commit()
    return merged


def rename_template(conn, key: str, name: str) -> dict:
    name = str(name or '').strip()
    if not name:
        raise ValueError('分类名称不能为空')
    template = get_template(conn, key)
    if template['storage'] != 'dynamic':
        raise ValueError('预置分类名称是固定配置，不能修改')
    conn.execute("UPDATE category_template SET name=?, updated_at=datetime('now') WHERE key=?",
                 (name, key))
    conn.commit()
    return {**template, 'name': name}


def approve_template(conn, draft: dict, expected_version: int | None = None) -> dict:
    value = validate_template(draft)
    current = conn.execute('SELECT version FROM category_template WHERE key=?', (value['key'],)).fetchone()
    if current:
        if expected_version is None or current['version'] != expected_version:
            raise ValueError('此工单已过期（分类模板已更新），请直接驳回；如需入库请重新发送 Excel')
        version = current['version'] + 1
        previous = _loads(conn.execute('SELECT quote_map_json FROM category_template WHERE key=?',
                                       (value['key'],)).fetchone()['quote_map_json'] or '{}', {})
        # 表头变了重推映射：保留仍然存在的显式绑定，缺的重新推荐。
        field_keys = {field['key'] for field in value['fields']}
        quote_map = {**suggest_quote_map(value['fields']),
                     **{name: key for name, key in previous.items() if key in field_keys}}
        conn.execute("UPDATE category_template SET name=?,supplier=COALESCE(NULLIF(?,''),supplier),version=?,"
                     "fields_json=?,status='approved',"
                     "storage=?,source_sheet=?,quote_map_json=?,updated_at=datetime('now') WHERE key=?",
                     (value['name'], str(value.get('supplier') or '')[:40], version,
                      json.dumps(value['fields'], ensure_ascii=False),
                      value['storage'], value['source_sheet'],
                      json.dumps(quote_map, ensure_ascii=False), value['key']))
    else:
        if expected_version not in (None, 0):
            raise ValueError('此工单已过期（分类模板已更新），请直接驳回；如需入库请重新发送 Excel')
        version = 1
        quote_map = suggest_quote_map(value['fields'])
        conn.execute('INSERT INTO category_template(key,name,supplier,version,fields_json,status,storage,source_sheet,quote_map_json) '
                     "VALUES(?,?,?,?,?,'approved',?,?,?)",
                     (value['key'], value['name'], str(value.get('supplier') or '')[:40], version,
                      json.dumps(value['fields'], ensure_ascii=False),
                      value['storage'], value['source_sheet'], json.dumps(quote_map, ensure_ascii=False)))
    snapshot = {**value, 'version': version}
    conn.execute('INSERT INTO category_template_version(category_key,version,snapshot_json) VALUES(?,?,?)',
                 (value['key'], version, json.dumps(snapshot, ensure_ascii=False)))
    return snapshot


def upsert_approved_products(conn, category_key: str, rows: list[dict], *,
                             source_key: str = '', source_sheet: str = '',
                             source_doc: int | None = None) -> dict:
    template = get_template(conn, category_key)
    if template['storage'] != 'dynamic':
        raise ValueError('预置分类继续使用原商品表')
    allowed = {field['key'] for field in template['fields']}
    created = updated = 0
    for raw in rows:
        product_id = str(raw.get('id') or secrets.token_hex(8))
        data = {key: '' if value is None else str(value) for key, value in (raw.get('data') or {}).items()
                if key in allowed}
        images = [str(value) for value in (raw.get('images') or []) if value]
        values = (category_key, str(raw.get('inner_code') or inner_code.gen()),
                  json.dumps(data, ensure_ascii=False), str(raw.get('status') or 'approved'),
                  images[0] if images else '', json.dumps(images, ensure_ascii=False), source_doc,
                  source_key, source_sheet, raw.get('source_row'), str(raw.get('row_fingerprint') or ''),
                  1 if raw.get('cs_visible') else 0)
        exists = conn.execute('SELECT 1 FROM product_dynamic WHERE id=?', (product_id,)).fetchone()
        if exists:
            conn.execute('UPDATE product_dynamic SET category_key=?,inner_code=?,data_json=?,status=?,image_main=?,'
                         'images_json=?,source_doc=?,source_key=?,source_sheet=?,source_row=?,row_fingerprint=?,'
                         'cs_visible=?,updated_at=datetime(\'now\') WHERE id=?', (*values, product_id))
            updated += 1
        else:
            conn.execute('INSERT INTO product_dynamic(id,category_key,inner_code,data_json,status,image_main,images_json,'
                         'source_doc,source_key,source_sheet,source_row,row_fingerprint,cs_visible) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (product_id, *values))
            created += 1
    return {'created': created, 'updated': updated}


def list_products(conn, category_key: str, *, public_only: bool = False) -> list[dict]:
    template = get_template(conn, category_key)
    where = " AND status='approved' AND cs_visible=1" if public_only else ''
    rows = conn.execute('SELECT * FROM product_dynamic WHERE category_key=?' + where +
                        ' ORDER BY COALESCE(source_row,2147483647),created_at,id',
                        (category_key,)).fetchall()
    fields = template['fields']
    model_keys = [field['key'] for field in fields if field['role'] == 'model']
    result = []
    for row in rows:
        data = _loads(row['data_json'], {})
        images = _loads(row['images_json'], [])
        if public_only:
            from . import price_policy
            specs = {field['label']: str(data.get(field['key'])) for field in fields
                     if field['visibility'] == 'public' and field['role'] not in {'image', 'cost', 'price'}
                     and field['type'] != 'money'
                     and data.get(field['key']) not in (None, '')
                     and price_policy.public_spec_allowed(field['label'], data.get(field['key']))}
            name = next((str(data.get(key)) for key in model_keys if data.get(key)), template['name'] + '商品')
            if not price_policy.public_spec_allowed('型号', name):
                name = '商品'
            category_name = template['name'] if price_policy.public_spec_allowed('分类', template['name']) else '商品'
            result.append({'id': row['id'], '_category': category_key, 'category_name': category_name,
                           'name': name, 'specs': specs, 'image_main': row['image_main'],
                           'images': images, 'cs_visible': 1, 'status': 'approved'})
        else:
            result.append({'id': row['id'], 'category_key': category_key, 'category_name': template['name'],
                           'inner_code': row['inner_code'], 'data': data, 'status': row['status'],
                           'image_main': row['image_main'], 'images': images, 'source_doc': row['source_doc'],
                           'source_key': row['source_key'], 'source_sheet': row['source_sheet'],
                           'source_row': row['source_row'], 'row_fingerprint': row['row_fingerprint'],
                           'cs_visible': row['cs_visible']})
    return result
