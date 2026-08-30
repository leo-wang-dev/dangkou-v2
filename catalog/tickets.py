"""审批工单：一切写操作必经此处（一次性 token）。AI 只产工单，不直接落库。"""
import json
import secrets

from . import inner_code
from .templates import TEMPLATES


class TicketError(Exception):
    pass


def create(conn, ticket_type, category, payload) -> dict:
    token = secrets.token_urlsafe(24)
    cur = conn.execute(
        'INSERT INTO approval_ticket(ticket_type, category, payload, token) VALUES(?,?,?,?)',
        (ticket_type, category, json.dumps(payload, ensure_ascii=False), token))
    conn.commit()
    return {'id': cur.lastrowid, 'token': token}


def decide(conn, ticket_id, token, approved: bool, decisions=None) -> dict:
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token_used_at'] is not None:
        raise TicketError('token 已使用（一次性）')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    if not approved:
        conn.execute("UPDATE approval_ticket SET status='rejected', "
                     "token_used_at=datetime('now'), decided_at=datetime('now') WHERE id=?",
                     (ticket_id,))
        conn.commit()
        return {'rejected': True}
    result = _apply(conn, json.loads(row['payload']), row['category'], decisions)
    conn.execute("UPDATE approval_ticket SET status='approved', "
                 "token_used_at=datetime('now'), decided_at=datetime('now') WHERE id=?",
                 (ticket_id,))
    conn.commit()
    return result


def _apply(conn, payload, category, decisions) -> dict:
    t = TEMPLATES[category]
    if payload.get('kind') == 'mutate':
        return _apply_mutate(conn, t, payload)
    drafts = payload['drafts']
    rejected = set((decisions or {}).get('reject', []))
    edits = (decisions or {}).get('edits') or {}   # {行钥匙: {col: 新值}} 审核时人工修正

    def _keep(d):
        rid = d.get('_rid') or d.get('id') if isinstance(d, dict) else d
        return rid not in rejected

    def _edited(d):
        rid = d.get('_rid') or d.get('id') if isinstance(d, dict) else None
        e = edits.get(rid) or edits.get(str(rid)) if rid is not None else None
        if not isinstance(e, dict):
            return d
        d = {**d, **e}
        imgs = e.get('__images')          # 审批时人工换图（_upload/ 暂存rel清单）
        if isinstance(imgs, list) and imgs:
            d['images'] = imgs
            d['image_main'] = imgs[0]
        return d

    created, created_rows = 0, []
    for d in drafts.get('new', []):
        if not _keep(d):
            continue
        d = _edited(d)
        pid = secrets.token_hex(8)
        cols_vals = [(c, str(d.get(c, '') or '')) for c, _ in t.fields]
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
        if not _keep(d):
            continue
        d = _edited(d)
        sets = ', '.join(f'{c}=?' for c, _ in t.fields)
        conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*[str(d.get(c, '') or '') for c, _ in t.fields], row['id']))
        updated += 1
    delisted = 0
    for r in drafts.get('delist', []):
        if isinstance(r, dict) and (r.get('_rid') or r.get('id')) in rejected:
            continue
        rid = r['id'] if isinstance(r, dict) else r
        conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (rid,))
        delisted += 1
    return {'created': created, 'updated': updated, 'delisted': delisted,
            'created_rows': created_rows, 'work_dir': payload.get('work_dir')}


def _apply_mutate(conn, t, payload) -> dict:
    action = payload['action']
    if action == 'update' and payload.get('images'):
        return {'mutated': 'update',
                'images_applied': {'table': t.table, 'id': payload['product_id'],
                                   'images': payload['images']}}
    if action == 'update':
        sets = ', '.join(f'{c}=?' for c in payload['changes'])
        conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*payload['changes'].values(), payload['product_id']))
    elif action == 'delete':
        conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (payload['product_id'],))
    elif action == 'create':
        cols = list(payload['changes'])
        conn.execute(f"INSERT INTO {t.table}(id, inner_code, {', '.join(cols)}) "
                     f"VALUES(?,?,{','.join('?' for _ in cols)})",
                     (secrets.token_hex(8), inner_code.gen(), *payload['changes'].values()))
    return {'mutated': action, 'created_rows': [], 'work_dir': None}


def decide_row(conn, ticket_id, token, row_key, approved, edits=None):
    """单行决策：只处理指定行，工单保持存活直到全部行处理完。"""
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    payload = json.loads(row['payload'])
    if payload.get('kind') != 'import':
        raise TicketError('仅支持导入工单的行级操作')

    # 记录已处理的行
    done = payload.setdefault('done_rows', {})
    done[row_key] = 'approved' if approved else 'rejected'

    t = TEMPLATES[row['category']]
    result = {'row': row_key, 'action': 'approved' if approved else 'rejected'}

    if approved:
        # 找到该行并单独落库
        for section in ('new', 'update', 'delist'):
            items = payload['drafts'].get(section, [])
            for i, item in enumerate(items):
                key = item.get('_rid') if isinstance(item, dict) else None
                if not key and section == 'update' and isinstance(item, list):
                    key = item[0].get('id')
                if not key and isinstance(item, dict):
                    key = item.get('id')
                if key == row_key:
                    single = _apply_single(conn, t, payload, section, i, edits)
                    if single:
                        result['created_rows'] = [single]
                        result['work_dir'] = payload.get('work_dir')
                    break

    # 检查是否全部处理完
    all_keys = set()
    for i, d in enumerate(payload['drafts'].get('new', [])):
        all_keys.add(d.get('_rid') or f'n{i}')
    for pair in payload['drafts'].get('update', []):
        r0 = pair[0] if isinstance(pair, list) else pair
        all_keys.add(r0.get('id') or f'u{payload["drafts"]["update"].index(pair)}')
    for r in payload['drafts'].get('delist', []):
        all_keys.add(r.get('id') if isinstance(r, dict) else r)
    if all_keys.issubset(set(done.keys())):
        # 全部处理完，关闭工单
        conn.execute("UPDATE approval_ticket SET status='approved', "
                     "decided_at=datetime('now'), token_used_at=datetime('now') WHERE id=?",
                     (ticket_id,))
    else:
        conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                     (json.dumps(payload, ensure_ascii=False), ticket_id))
    conn.commit()
    return result


def _apply_single(conn, t, payload, section, idx, edits=None):
    """落库工单中指定位置的单行。"""
    import secrets as _sec
    from . import inner_code as _ic
    if section == 'new':
        d = payload['drafts']['new'][idx]
        if edits:
            d = {**d, **edits}
            imgs = edits.get('__images')
            if isinstance(imgs, list) and imgs:
                d['images'] = imgs
                d['image_main'] = imgs[0]
        pid = _sec.token_hex(8)
        cols_vals = [(c, str(d.get(c, '') or '')) for c, _ in t.fields]
        conn.execute(
            f"INSERT INTO {t.table}(id, inner_code, {', '.join(c for c, _ in cols_vals)}, "
            f"image_main, images, source_doc) VALUES({','.join('?' for _ in range(3 + len(cols_vals) + 2))})",
            (pid, _ic.gen(), *[v for _, v in cols_vals],
             d.get('image_main') or '',
             json.dumps(d.get('images') or [], ensure_ascii=False),
             payload.get('doc_id')))
        return {'id': pid, 'image_main': d.get('image_main') or '',
                'images': d.get('images') or [],
                '_category': t.key, '_table': t.table}
    elif section == 'update':
        pair = payload['drafts']['update'][idx]
        row_d, d = pair if isinstance(pair, list) else (pair, pair)
        sets = ', '.join(f'{c}=?' for c, _ in t.fields)
        conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                     (*[str(d.get(c, '') or '') for c, _ in t.fields], row_d['id']))
    elif section == 'delist':
        r = payload['drafts']['delist'][idx]
        rid = r['id'] if isinstance(r, dict) else r
        conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (rid,))


def save_draft_edit(conn, ticket_id, token, row_key, edits):
    """编辑草稿立即持久化到工单payload（不决策，用户可见改后效果）。"""
    row = conn.execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
    if row is None:
        raise TicketError('工单不存在')
    if row['token'] != token or row['status'] != 'pending':
        raise TicketError('token 无效或工单已决')
    payload = json.loads(row['payload'])
    if payload.get('kind') != 'import':
        raise TicketError('仅支持导入工单')

    for section in ('new', 'update', 'delist'):
        items = payload['drafts'].get(section, [])
        for i, item in enumerate(items):
            if not isinstance(item, dict) and not isinstance(item, list):
                continue
            d = item if isinstance(item, dict) else item[1]
            key = d.get('_rid') or (item[0].get('id') if isinstance(item, list) else d.get('id'))
            if key == row_key:
                imgs = edits.pop('__images', None)
                if imgs:
                    d['images'] = imgs
                    d['image_main'] = imgs[0]
                d.update(edits)
                break

    conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',
                 (json.dumps(payload, ensure_ascii=False), ticket_id))
    conn.commit()
    return {'saved': True, 'row': row_key}
