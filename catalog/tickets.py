"""审批工单：一切写操作必经此处（一次性 token）。AI 只产工单，不直接落库。"""
import json
import hashlib
import secrets
import threading
from functools import wraps

from . import inner_code


class TicketError(Exception):
    pass


class TicketConflict(TicketError):
    pass


# Serialize users of a shared connection, and acquire SQLite's cross-process writer
# lock before reading ticket state. Roll back both mutations and decisions together.
_lock = threading.RLock()
def atomic(fn):
    @wraps(fn)
    def wrapped(conn, *args, **kwargs):
        with _lock:
            if conn.in_transaction:
                raise TicketError('数据库忙，请重试')
            conn.execute('BEGIN IMMEDIATE')
            try:
                result = fn(conn, *args, **kwargs)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
    return wrapped


def create(conn, ticket_type, category, payload) -> dict:
    token = secrets.token_urlsafe(24)
    cur = conn.execute(
        'INSERT INTO approval_ticket(ticket_type, category, payload, token) VALUES(?,?,?,?)',
        (ticket_type, category, json.dumps(payload, ensure_ascii=False), token))
    conn.commit()
    return {'id': cur.lastrowid, 'token': token}


@atomic
def decide(conn, ticket_id, token, approved: bool, decisions=None, before_commit=None) -> dict:
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token_used_at'] is not None:
        raise TicketError('token 已使用（一次性）')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    if not approved:
        try:
            rejected_payload = json.loads(row['payload'])
            phase = rejected_payload.get('phase')
            doc_id = rejected_payload.get('doc_id')
            if phase in ('template', 'products') and doc_id:
                status = 'template_rejected' if phase == 'template' else 'product_rejected'
                conn.execute('UPDATE import_doc SET status=?, stats_json=? WHERE id=?',
                             (status, json.dumps({'phase': phase, 'rejected': True}, ensure_ascii=False), doc_id))
        except (TypeError, ValueError):
            pass
        conn.execute("UPDATE approval_ticket SET status='rejected', "
                     "token_used_at=datetime('now'), decided_at=datetime('now') WHERE id=?",
                     (ticket_id,))
        return {'rejected': True}
    payload = json.loads(row['payload'])
    result = _apply(conn, payload, row['category'], decisions)
    if payload.get('phase') == 'products' and payload.get('doc_id'):
        conn.execute("UPDATE import_doc SET status='product_approved', stats_json=? WHERE id=?",
                     (json.dumps({'phase': 'products',
                                  'template_doc_id': payload.get('template_doc_id'),
                                  'created': result.get('created', 0),
                                  'updated': result.get('updated', 0),
                                  'delisted': result.get('delisted', 0)}, ensure_ascii=False),
                      payload['doc_id']))
    if before_commit:
        before_commit(result)
    conn.execute("UPDATE approval_ticket SET status='approved', "
                 "token_used_at=datetime('now'), decided_at=datetime('now') WHERE id=?",
                 (ticket_id,))
    return result


def _apply(conn, payload, category, decisions) -> dict:
    if payload.get('kind') == 'redline':            # C端：红线知识（批准即写入）
        from . import cs
        current = cs.get_redline(conn, payload.get('product_id'))
        if 'old_text_raw' in payload and current['text_raw'] != payload['old_text_raw']:
            raise TicketConflict('红线已更新，请重新提交审批，避免覆盖新规则')
        cs.set_redline(conn, payload.get('product_id'),
                       payload['text_raw'], payload.get('text_summary'), commit=False)
        return {'mutated': 'redline'}
    if payload.get('kind') == 'shop':
        from . import cs
        from .shop_link import validate_binding
        try:
            changes = cs.validate_shop(payload['changes'])
            from . import merchant_policy
            if (merchant_policy.read(conn) or {}).get('wechat_managed') and {'tg_bot_id','tg_bot_username'} & changes.keys():
                raise ValueError('客户Bot身份只能通过微信Token验证绑定')
            validate_binding(conn,changes)
        except ValueError as exc:
            raise TicketError(str(exc)) from exc
        current=cs.get_shop(conn)
        if any(current.get(k) != payload.get('old',{}).get(k) for k in changes):
            raise TicketConflict('档口资料已更新，请重新提交审批')
        conn.execute('UPDATE shop_profile SET ' + ', '.join(f'{k}=?' for k in changes) +
                     ", updated_at=datetime('now') WHERE id=1", tuple(changes.values()))
        return {'mutated': 'shop'}
    if payload.get('kind') == 'template_import':
        from . import dynamic_import
        return dynamic_import.apply_ticket(conn, payload, decisions)
    if payload.get('kind') == 'dynamic_mutate':
        return _apply_dynamic_mutate(conn, payload, category)
    raise TicketError('不支持的工单类型：'
                      + str(payload.get('kind') or '未知')
                      + '（旧版固定品类导入/改价工单已下线，请驳回后改用动态分类流程）')


def _dynamic_product_snapshot(row) -> str:
    if row is None:
        return ''
    value = {key: row.get(key) for key in (
        'id', 'inner_code', 'data', 'status', 'images', 'source_doc', 'source_key',
        'source_sheet', 'source_row', 'row_fingerprint', 'cs_visible')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _apply_dynamic_mutate(conn, payload, category):
    from . import dynamic_catalog
    try:
        template = dynamic_catalog.get_template(conn, category)
    except KeyError as exc:
        raise TicketError('未知品类') from exc
    if template['storage'] != 'dynamic' or template['version'] != payload.get('template_version'):
        raise TicketConflict('分类模板已经变化，请重新提交商品操作')
    rows = dynamic_catalog.list_products(conn, category)
    action = payload['action']
    current = next((row for row in rows if row['id'] == payload.get('product_id')), None)
    if action != 'create':
        if current is None:
            raise TicketConflict('商品已经不存在，请刷新后重试')
        if _dynamic_product_snapshot(current) != payload.get('before_snapshot'):
            raise TicketConflict('商品数据已变化，请刷新后重新提交')
    changes = payload.get('changes') or {}
    visible_change = changes.get('cs_visible')
    if visible_change is not None and str(visible_change) not in {'0', '1'}:
        raise TicketError('可观测只能是 0 或 1')
    if action == 'delete':
        conn.execute("UPDATE product_dynamic SET status='delisted',updated_at=datetime('now') "
                     'WHERE id=? AND category_key=?', (current['id'], category))
        conn.execute('DELETE FROM embedding WHERE product_id=?', (current['id'],))
        return {'mutated': 'delete', 'created': 0, 'updated': 0, 'delisted': 1}
    allowed = {field['key'] for field in template['fields']}
    data_changes = {key: str(value) for key, value in changes.items() if key in allowed}
    if action == 'create':
        product_id = secrets.token_hex(8)
        row = {'id': product_id, 'inner_code': inner_code.gen(), 'data': data_changes,
               'images': payload.get('images') or [],
               'cs_visible': int(str(visible_change or '0')), 'status': 'approved'}
        outcome = dynamic_catalog.upsert_approved_products(conn, category, [row])
        created_rows = ([{'id': product_id, '_category': category, '_table': 'product_dynamic',
                          'images': row['images'], 'image_main': row['images'][0]}]
                        if row['images'] else [])
        return {'mutated': 'create', **outcome, 'delisted': 0, 'created_rows': created_rows}
    data = {**current['data'], **data_changes}
    images = payload['images'] if 'images' in payload else current['images']
    if 'images' in payload:
        conn.execute('DELETE FROM embedding WHERE product_id=?', (current['id'],))
    row = {**current, 'data': data, 'images': images,
           'cs_visible': int(str(visible_change)) if visible_change is not None else current['cs_visible']}
    outcome = dynamic_catalog.upsert_approved_products(
        conn, category, [row], source_key=current['source_key'],
        source_sheet=current['source_sheet'], source_doc=current['source_doc'])
    created_rows = ([{'id': current['id'], '_category': category, '_table': 'product_dynamic',
                      'images': images, 'image_main': images[0]}]
                    if 'images' in payload and images else [])
    return {'mutated': 'update', **outcome, 'delisted': 0, 'created_rows': created_rows}


@atomic
def save_draft_edit(conn, ticket_id, token, row_key, edits):
    """编辑草稿立即持久化到工单payload（不决策，用户可见改后效果）。"""
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    payload = json.loads(row['payload'])
    if payload.get('kind') == 'template_import':
        found = False
        for sheet in payload.get('sheets', []):
            allowed = {field['key'] for field in sheet['template']['fields']}
            for section in ('new', 'update'):
                for index, item in enumerate(sheet.get('drafts', {}).get(section, [])):
                    old, draft = (item[0], item[1]) if isinstance(item, list) else (item, item)
                    key = (old.get('id') if isinstance(item, list) else draft.get('_rid')) or f'n{index}'
                    if str(key) != str(row_key):
                        continue
                    changes = dict(edits)
                    images = changes.pop('__images', None)
                    draft['data'] = {**draft.get('data', {}),
                                     **{name: value for name, value in changes.items() if name in allowed}}
                    if images is not None:
                        draft['images'] = images
                        draft['image_main'] = images[0] if images else ''
                    found = True
                    break
        if not found:
            raise TicketError('草稿行不存在')
        conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                     (json.dumps(payload, ensure_ascii=False), ticket_id))
        return {'saved': True, 'row': row_key}
    raise TicketError('仅支持动态分类导入工单的行级编辑')
