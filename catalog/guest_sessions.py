"""Issued anonymous capabilities. Call validate before work and touch under commit lock.

Only hashes persist. 24h inactivity is a configurable technical retention default,
not a use quota. Browser-session loss alone cannot notify the server reliably.
"""
import hashlib
import os
import secrets
import time
from fastapi import HTTPException


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS guest_sessions(
        token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL,
        last_active_at REAL NOT NULL, expires_at REAL NOT NULL,
        photo_mode TEXT NOT NULL DEFAULT '', pending_photo TEXT NOT NULL DEFAULT '')''')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cs_link'").fetchone():
        conn.execute("""UPDATE cs_link SET expires_at='1970-01-01' WHERE NOT EXISTS (
            SELECT 1 FROM cs_customer c JOIN guest_sessions s ON s.owner_id=c.tg_id
            WHERE c.id=cs_link.customer_id)""")


def ttl():
    return max(60, float(os.environ.get('GUEST_IDLE_SECONDS', '86400')))


def issue(conn):
    token = 'guest-' + secrets.token_hex(24)
    now = time.time()
    owner = 'h5-' + secrets.token_hex(16)
    conn.execute('INSERT INTO guest_sessions(token_hash,owner_id,created_at,last_active_at,expires_at) VALUES(?,?,?,?,?)',
                 (digest(token), owner, now, now, now + ttl()))
    return token


def validate(conn, token):
    return validate_hash(conn, digest(str(token)))


def validate_hash(conn, token_hash):
    row = conn.execute('SELECT * FROM guest_sessions WHERE token_hash=?', (token_hash,)).fetchone()
    if row is None:
        raise HTTPException(401, 'session_required')
    if row['state'] != 'active' or row['expires_at'] <= time.time():
        raise HTTPException(410, 'session_expired')
    return row


def touch(conn, token):
    return touch_hash(conn, digest(str(token)))


def touch_hash(conn, token_hash):
    row = validate_hash(conn, token_hash)
    now = time.time()
    conn.execute('UPDATE guest_sessions SET last_active_at=?,expires_at=? WHERE token_hash=?',
                 (now, now + ttl(), row['token_hash']))
    return row


def revoke(conn, token, state='revoked'):
    row = validate(conn, token)
    conn.execute('UPDATE guest_sessions SET state=? WHERE token_hash=?', (state, row['token_hash']))
    return row


def purge_shop(conn):
    """Return files eligible for post-commit deletion, leaving merged owners intact."""
    stale = conn.execute("SELECT * FROM guest_sessions WHERE state IN ('active','revoked') AND (state='revoked' OR expires_at<=?)", (time.time(),)).fetchall()
    paths = []
    for session in stale:
        if session['pending_photo']:
            paths.append(session['pending_photo'])
        customer = conn.execute('SELECT id FROM cs_customer WHERE tg_id=?',(session['owner_id'],)).fetchone()
        if customer:
            owner = customer['id']
            paths.extend(r[0] for r in conn.execute('SELECT photo FROM cs_note WHERE customer_id=?',(owner,)) if r[0])
            for table in ('cs_note','cs_link','cs_conversation_log','cs_card_info','cs_context','cs_photo_candidates'):
                conn.execute(f'DELETE FROM {table} WHERE customer_id=?',(owner,))
            conn.execute("DELETE FROM note_batches WHERE owner_kind='guest' AND owner_id=?",(owner,))
            conn.execute('DELETE FROM cs_customer WHERE id=?',(owner,))
        conn.execute("UPDATE guest_sessions SET state='expired',pending_photo='' WHERE token_hash=?",(session['token_hash'],))
    return paths


def remove_shop_files(conn, paths):
    base = os.path.realpath(os.environ.get('CATALOG_CS_PHOTOS') or os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'cs_photos'))
    for path in set(paths):
        if not path or not os.path.realpath(path).startswith(base + os.sep):
            continue
        if conn.execute('SELECT 1 FROM cs_note WHERE photo=?',(path,)).fetchone():
            continue
        if conn.execute('SELECT 1 FROM guest_sessions WHERE pending_photo=?',(path,)).fetchone():
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def purge_central(conn):
    stale = conn.execute("SELECT owner_id FROM guest_sessions WHERE state IN ('active','revoked') AND (state='revoked' OR expires_at<=?)", (time.time(),)).fetchall()
    paths = []
    for row in stale:
        owner = row['owner_id']
        paths.extend(n[0] for n in conn.execute("SELECT photo_path FROM notes WHERE owner_kind='guest' AND owner_id=?", (owner,)))
        conn.execute("DELETE FROM notes WHERE owner_kind='guest' AND owner_id=?", (owner,))
        conn.execute("DELETE FROM note_batches WHERE owner_kind='guest' AND owner_id=?", (owner,))
        conn.execute("UPDATE guest_sessions SET state='expired' WHERE owner_id=?", (owner,))
    return paths


def sweep(conn, kind, photo_dir):
    """Periodic retention job; commit row cleanup before reference-aware file deletion.

    Expired capability tombstones are retained for one day (clear 410 responses),
    then removed. Unknown old orphan files are removed only after an idle window.
    """
    if kind not in ('central','shop'):
        raise ValueError('kind must be central or shop')
    paths = purge_central(conn) if kind == 'central' else purge_shop(conn)
    conn.execute("DELETE FROM guest_sessions WHERE state!='active' AND expires_at<?", (time.time()-86400,))
    conn.commit()
    base = os.path.realpath(photo_dir)
    if os.path.isdir(base):
        paths.extend(entry.path for entry in os.scandir(base)
                     if entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < time.time()-ttl())
    table, field = ('notes','photo_path') if kind == 'central' else ('cs_note','photo')
    for path in set(paths):
        if not path or not os.path.realpath(path).startswith(base+os.sep):
            continue
        if conn.execute(f'SELECT 1 FROM {table} WHERE {field}=?',(path,)).fetchone():
            continue
        if conn.execute('SELECT 1 FROM guest_sessions WHERE pending_photo=?',(path,)).fetchone():
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    import argparse
    import sqlite3
    parser = argparse.ArgumentParser(description='Purge expired anonymous session data and unreferenced photos')
    parser.add_argument('--db',required=True)
    parser.add_argument('--kind',choices=('central','shop'),required=True)
    parser.add_argument('--photo-dir',required=True)
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        sweep(connection,args.kind,args.photo_dir)
    finally:
        connection.close()
