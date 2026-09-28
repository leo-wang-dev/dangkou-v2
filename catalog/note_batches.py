"""Supplier cards belong to immutable collection batches, never the whole buyer."""
import json
from fastapi import HTTPException
from .cs_supplier import FIELDS, normalize


def migrate(conn, table):
    conn.execute('''CREATE TABLE IF NOT EXISTS note_batches(
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
        state TEXT NOT NULL, fields_json TEXT NOT NULL DEFAULT '{}', preset_basis TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 0)''')
    if 'batch_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN batch_id INTEGER REFERENCES note_batches(id)')


def create(conn, kind, owner, fields=None, state='unassigned', preset_basis=''):
    clean = {k: str(v).strip()[:300] for k,v in (fields or {}).items()
             if k in FIELDS and str(v or '').strip() not in ('', '未拍到', '待补充', '模糊')}
    cur = conn.execute('INSERT INTO note_batches(owner_kind,owner_id,state,fields_json,preset_basis) VALUES(?,?,?,?,?)',
                       (kind, owner, state, json.dumps(clean,ensure_ascii=False),preset_basis))
    return cur.lastrowid


def prepare(conn, kind, owner, cards, preset=None):
    pending = [create(conn,kind,owner,c,'pending') for c in cards]
    current = conn.execute("SELECT id FROM note_batches WHERE owner_kind=? AND owner_id=? AND active=1 AND state='confirmed'", (kind,owner)).fetchone()
    unresolved = conn.execute("SELECT 1 FROM note_batches WHERE owner_kind=? AND owner_id=? AND state='pending' LIMIT 1",(kind,owner)).fetchone()
    # A card photographed with goods does not establish which goods it describes.
    if unresolved or not current:
        if preset and not unresolved:
            bid = create(conn,kind,owner,preset,'confirmed','receiving_shop_preset')
            conn.execute('UPDATE note_batches SET active=1 WHERE id=?',(bid,))
            current = (bid,)
        else:
            current = (create(conn,kind,owner),)
    return current[0], pending


def listing(conn, kind, owner):
    return [{**dict(r), 'fields':json.loads(r['fields_json'])} for r in conn.execute(
        'SELECT * FROM note_batches WHERE owner_kind=? AND owner_id=? ORDER BY id',(kind,owner))]


def confirm(conn, kind, owner, batch_id, note_ids, table):
    batch = conn.execute('SELECT * FROM note_batches WHERE id=? AND owner_kind=? AND owner_id=?', (batch_id,kind,owner)).fetchone()
    if not batch:
        raise HTTPException(404,'batch_not_found')
    owner_sql = 'customer_id=?' if table == 'cs_note' else 'owner_kind=? AND n.owner_id=?'
    args = (owner,) if table == 'cs_note' else (kind,owner)
    for note_id in note_ids:
        row = conn.execute(f'SELECT n.id,b.state FROM {table} n LEFT JOIN note_batches b ON b.id=n.batch_id WHERE n.id=? AND n.{owner_sql}',(note_id,*args)).fetchone()
        if not row or row['state'] not in (None,'unassigned'):
            raise HTTPException(409,'association_requires_unassigned_note')
    conn.execute("UPDATE note_batches SET state='declined' WHERE owner_kind=? AND owner_id=? AND state='pending' AND id!=?",(kind,owner,batch_id))
    conn.execute('UPDATE note_batches SET active=0 WHERE owner_kind=? AND owner_id=?',(kind,owner))
    conn.execute("UPDATE note_batches SET state='confirmed',active=1 WHERE id=?",(batch_id,))
    for note_id in note_ids:
        conn.execute(f'UPDATE {table} SET batch_id=? WHERE id=?',(batch_id,note_id))


def decline(conn, kind, owner, batch_id):
    changed = conn.execute("UPDATE note_batches SET state='declined' WHERE id=? AND owner_kind=? AND owner_id=? AND state='pending'",(batch_id,kind,owner))
    if not changed.rowcount:
        raise HTTPException(404,'pending_batch_not_found')


def export_group(note):
    """Exact complete normalized card identity is sufficient; name alone is not."""
    import unicodedata
    card = note.get('batch_fields') or {}
    identity = tuple(' '.join(unicodedata.normalize('NFKC', str(card.get(k) or '')).split()) for k in FIELDS)
    uncertain = ('未拍到','待补充','模糊','待确认','未知','unknown')
    required = ('档口名称', '档口号/地址', '供应商联系方式')
    has_identity = all(identity[FIELDS.index(key)] for key in required)
    if has_identity and not any(marker in value.casefold() for value in identity for marker in uncertain):
        return ('supplier', identity)
    return ('batch', note.get('batch_id', 'legacy'))


def project(conn, note):
    note = dict(note)
    fields = normalize(json.loads(note['fields_json']))
    batch = conn.execute('SELECT * FROM note_batches WHERE id=?',(note.get('batch_id'),)).fetchone()
    card = json.loads(batch['fields_json']) if batch and batch['state']=='confirmed' else {}
    fields.update(card)
    return {**note, 'fields_json':json.dumps(fields,ensure_ascii=False),
            'batch_id':note.get('batch_id') or 'legacy', 'batch_fields':card,
            'batch_state':batch['state'] if batch else 'legacy_unassigned'}
