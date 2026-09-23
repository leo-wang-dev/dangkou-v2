"""审批工单：一切写操作必经此处（一次性 token）。AI 只产工单，不直接落库。"""
import json
import hashlib
import secrets
import threading
from functools import wraps

from . import inner_code
from .templates import TEMPLATES

# C端可见性字段；已移除的阶梯价在所有写入和审批入口拒绝。
# 键=AI 可用的中文名，值=落库列名；normalize 后直接以列名进入 changes。
CS_FIELDS = {'可观测': 'cs_visible', '对客户可见': 'cs_visible'}


class TicketError(Exception):
    pass


class TicketConflict(TicketError):
    pass


def _check_import_snapshot(conn, payload, category):
    if payload.get('kind') != 'import':
        return
    if 'source_snapshot' not in payload:
        if payload.get('doc_id') and conn.execute('SELECT 1 FROM import_doc WHERE id=?',(payload['doc_id'],)).fetchone():
            raise TicketConflict('旧导入工单缺少数据版本，无法确认是否过期。请驳回后重新导入核对。')
        return
    from . import import_snapshot
    if import_snapshot.current(conn, category, payload['source_key']) != payload['source_snapshot']:
        raise TicketConflict('商品数据已变化，当前导入工单已过期。请驳回后重新导入并核对，避免覆盖新数据。')


def _refresh_import_snapshot(conn, payload, category):
    if 'source_snapshot' in payload:
        from . import import_snapshot
        payload['source_snapshot'] = import_snapshot.current(conn, category, payload['source_key'])



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


def normalize_changes(t, changes: dict):
    """字段把关（不做翻译——语义映射是 AI 的活，工具描述里给了字段清单）。

    合法键（模板label 或 列名）→ 统一成 label 键保留；
    非法键不丢：值以"原字段：值"拼进"备注"（｜连接）。
    建 单时调（B层：全非法→上层 400 让 AI 自纠）、决策时调（C层：存量脏单保命不 500）。
    返回 (归一 changes, 进备注的原键清单)。
    """
    from .cs import reject_tiers
    try:
        reject_tiers(changes)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    col_to_label = {col: label for col, label in t.fields}
    legal = {**{label: label for label in col_to_label.values()}, **col_to_label,
             **CS_FIELDS}
    cs_cols = set(CS_FIELDS.values())             # 二次归一：已归一的列名直通
    norm, extra, to_remark = {}, [], []
    for k, v in (changes or {}).items():
        if v is None or str(v).strip() == '':
            continue
        if k in CS_FIELDS:                        # C端列：中文名 → 列名
            norm[CS_FIELDS[k]] = str(v).strip()
        elif k in cs_cols:
            norm[k] = str(v).strip()
        elif k in legal:
            norm[legal[k]] = str(v).strip()
        else:
            extra.append(f'{k}：{str(v).strip()}')
            to_remark.append(k)
    if extra:
        norm['备注'] = norm['备注'] + '｜' + '｜'.join(extra) if norm.get('备注') \
            else '｜'.join(extra)
    return norm, to_remark


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
    _check_import_snapshot(conn, payload, row['category'])
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


def _import_values(conn, t, draft, pid=None):
    from . import cs
    try:
        cs.reject_tiers(draft)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    values = {c: str(draft.get(c, '') or '') for c, _ in t.fields}
    values.update({c: draft[c] for c in ('cs_visible',) if c in draft})
    try:
        cs.validate_product(conn, t, pid, values)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    return list(values.items())


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
    t = TEMPLATES[category]
    if payload.get('kind') == 'mutate':
        return _apply_mutate(conn, t, payload)
    drafts = payload['drafts']
    rejected = {str(k) for k in (decisions or {}).get('reject', [])} | set(payload.get('done_rows', {}))
    edits = (decisions or {}).get('edits') or {}   # {行钥匙: {col: 新值}} 审核时人工修正

    def _keep(d):
        rid = d.get('_rid') or d.get('id') if isinstance(d, dict) else d
        return str(rid) not in rejected

    def _edited(d):
        rid = d.get('_rid') or d.get('id') if isinstance(d, dict) else None
        e = edits.get(rid) or edits.get(str(rid)) if rid is not None else None
        if not isinstance(e, dict):
            return d
        d = {**d, **e}
        imgs = e.get('__images')          # 审批时人工换图（_upload/ 暂存rel清单）
        if isinstance(imgs, list):
            d['images'] = imgs
            d['image_main'] = imgs[0] if imgs else ''
        return d

    created, created_rows = 0, []
    for i, d in enumerate(drafts.get('new', [])):
        d = {**d, '_rid': d.get('_rid') or f'n{i}'}
        if not _keep(d):
            continue
        d = _edited(d)
        pid = secrets.token_hex(8)
        cols_vals = _import_values(conn, t, d)
        conn.execute(
            f"INSERT INTO {t.table}(id, inner_code, {', '.join(c for c, _ in cols_vals)}, "
            f"image_main, images, source_doc) VALUES({','.join('?' for _ in range(3 + len(cols_vals) + 2))})",
            (pid, inner_code.gen(), *[v for _, v in cols_vals],
             d.get('image_main') or '',
             json.dumps(d.get('images') or [], ensure_ascii=False),
             payload.get('doc_id')))
        created_rows.append({'id': pid, 'image_main': d.get('image_main') or '',
                             'images': d.get('images') or
                                       ([d['image_main']] if d.get('image_main') else []),
                             '_category': category, '_table': t.table})
        created += 1
    updated = 0
    for pair in drafts.get('update', []):
        row, d = pair if isinstance(pair, list) else (pair, pair)
        if not _keep(row):
            continue
        d = _edited({**d, '_rid': row.get('_rid') or row['id']})
        values = _import_values(conn, t, d, row['id'])
        sets = ', '.join(f'{c}=?' for c, _ in values)
        conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*[v for _, v in values], row['id']))
        if 'images' in d:
            if not d['images']:
                conn.execute(f"UPDATE {t.table} SET image_main='',images='[]' WHERE id=?", (row['id'],))
                conn.execute('DELETE FROM embedding WHERE product_id=?', (row['id'],))
            else:
                created_rows.append({'id': row['id'], 'images': d['images'], '_category': category, '_table': t.table})
        updated += 1
    delisted = 0
    for r in drafts.get('delist', []):
        if not _keep(r):
            continue
        rid = r['id'] if isinstance(r, dict) else r
        conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (rid,))
        delisted += 1
    return {'created': created, 'updated': updated, 'delisted': delisted,
            'created_rows': created_rows, 'work_dir': payload.get('work_dir')}


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


def _apply_mutate(conn, t, payload) -> dict:
    action = payload['action']
    # 字段把关（合法保留/非法进备注）再统一成列名——存量脏工单也能决策，不再 500
    label_to_col = {label: col for col, label in t.fields}
    norm, _ = normalize_changes(t, payload.get('changes') or {})
    changes = {label_to_col.get(k, k): v for k, v in norm.items()}
    if action in ('update', 'create'):
        from . import cs
        try:
            cs.validate_product(conn, t, payload.get('product_id') if action == 'update' else None, changes)
        except ValueError as exc:
            raise TicketError(str(exc)) from exc
    if action == 'update':
        if changes:
            sets = ', '.join(f'{c}=?' for c in changes)
            conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                         (*changes.values(), payload['product_id']))
        if payload.get('images') is not None:
            return {'mutated': 'update',
                    'images_applied': {'table': t.table, 'id': payload['product_id'],
                                       'images': payload['images']}}
        return {'mutated': 'update', 'created_rows': [], 'work_dir': None}
    if action == 'delete':
        conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (payload['product_id'],))
    elif action == 'create':
        import secrets as _sec
        from . import inner_code as _ic
        if not changes:
            return {'mutated': 'create', 'note': '无可识别字段，未落库',
                    'created_rows': [], 'work_dir': None}
        cols = list(changes)
        pid = _sec.token_hex(8)
        conn.execute(f"INSERT INTO {t.table}(id, inner_code, {', '.join(cols)}) "
                     f"VALUES(?,?,{','.join('?' for _ in cols)})",
                     (pid, _ic.gen(), *changes.values()))
        result = {'mutated': action, 'created_rows': [], 'work_dir': None}
        if payload.get('images') is not None:
            result['images_applied'] = {'table': t.table, 'id': pid,
                                        'images': payload['images']}
        return result
    return {'mutated': action, 'created_rows': [], 'work_dir': None}


@atomic
def decide_row(conn, ticket_id, token, row_key, approved, edits=None, before_commit=None):
    """单行决策：只处理指定行，工单保持存活直到全部行处理完。"""
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    payload = json.loads(row['payload'])
    if payload.get('kind') != 'import':
        raise TicketError('仅支持导入工单的行级操作')

    row_key = str(row_key)
    done = payload.setdefault('done_rows', {})
    if row_key in done:
        return {'row': row_key, 'action': done[row_key], 'already_done': True}
    found = None
    for section in ('new', 'update', 'delist'):
        for i, item in enumerate(payload['drafts'].get(section, [])):
            base = item[0] if isinstance(item, list) else item
            key = (base.get('_rid') or base.get('id') or f'n{i}') if isinstance(base, dict) else base
            if str(key) == row_key:
                found = (section, i)
    if found is None:
        raise TicketError('草稿行不存在')
    result = {'row': row_key, 'action': 'approved' if approved else 'rejected'}
    if approved:
        _check_import_snapshot(conn, payload, row['category'])
        section, i = found
        single = _apply_single(conn, TEMPLATES[row['category']], payload, section, i, edits)
        if single:
            result['created_rows'] = [single]
            result['work_dir'] = payload.get('work_dir')
    if approved and before_commit:
        before_commit(result)
    if approved:
        _refresh_import_snapshot(conn, payload, row['category'])
    done[row_key] = result['action']

    # 检查是否全部处理完
    all_keys = set()
    for i, d in enumerate(payload['drafts'].get('new', [])):
        all_keys.add(d.get('_rid') or f'n{i}')
    for pair in payload['drafts'].get('update', []):
        r0 = pair[0] if isinstance(pair, list) else pair
        all_keys.add(r0.get('id') or f'u{payload["drafts"]["update"].index(pair)}')
    for r in payload['drafts'].get('delist', []):
        all_keys.add(r.get('id') if isinstance(r, dict) else r)
    conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                 (json.dumps(payload, ensure_ascii=False), ticket_id))
    if {str(k) for k in all_keys}.issubset(set(done.keys())):
        # 全部处理完，关闭工单
        conn.execute("UPDATE approval_ticket SET status='approved', "
                     "decided_at=datetime('now'), token_used_at=datetime('now') WHERE id=?",
                     (ticket_id,))
    else:
        conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                     (json.dumps(payload, ensure_ascii=False), ticket_id))
    return result


def _apply_single(conn, t, payload, section, idx, edits=None):
    """Use the same validation and persistence as whole-ticket approval."""
    item = payload['drafts'][section][idx]
    base = item[0] if isinstance(item, list) else item
    key = (base.get('_rid') or base.get('id') or f'n{idx}') if isinstance(base, dict) else base
    if section == 'new' and not base.get('_rid'):
        item = {**item, '_rid': key}
    single = {**payload, 'done_rows': {}, 'drafts': {section: [item]}}
    result = _apply(conn, single, t.key, {'edits': {str(key): edits or {}}})
    return next(iter(result['created_rows']), None)


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
    if payload.get('kind') != 'import':
        raise TicketError('仅支持导入工单')

    if str(row_key) in payload.get('done_rows', {}):
        raise TicketError('该行已处理')
    edits = dict(edits)
    found = False
    for section in ('new', 'update', 'delist'):
        items = payload['drafts'].get(section, [])
        for i, item in enumerate(items):
            if not isinstance(item, dict) and not isinstance(item, list):
                continue
            d = item if isinstance(item, dict) else item[1]
            key = d.get('_rid') or (item[0].get('id') if isinstance(item, list) else d.get('id')) or f'n{i}'
            if str(key) == str(row_key):
                found = True
                imgs = edits.pop('__images', None)
                if imgs is not None:
                    d['images'] = imgs
                    d['image_main'] = imgs[0] if imgs else ''
                d.update(edits)
                break

    if not found:
        raise TicketError('草稿行不存在')
    conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                 (json.dumps(payload, ensure_ascii=False), ticket_id))
    return {'saved': True, 'row': row_key}
