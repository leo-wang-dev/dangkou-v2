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
        return {**d, **e} if isinstance(e, dict) else d

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
