"""Merchant-local account ownership and idempotent guest claim."""
import secrets
import time

from fastapi import HTTPException

from . import guest_sessions


def migrate(conn):
    columns = {row[1] for row in conn.execute('PRAGMA table_info(cs_customer)')}
    if 'account_id' not in columns:
        conn.execute("ALTER TABLE cs_customer ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cs_customer_account ON cs_customer(account_id) WHERE account_id!=''")
    columns = {row[1] for row in conn.execute('PRAGMA table_info(guest_sessions)')}
    if 'claimed_account_id' not in columns:
        conn.execute("ALTER TABLE guest_sessions ADD COLUMN claimed_account_id TEXT NOT NULL DEFAULT ''")


def owner_id(account_id: str) -> str:
    return 'h5-acct-' + account_id


def account_session(conn, account_id: str):
    owner = owner_id(account_id)
    row = conn.execute('SELECT * FROM guest_sessions WHERE owner_id=?', (owner,)).fetchone()
    if row is None:
        now = time.time()
        conn.execute('INSERT OR IGNORE INTO guest_sessions(token_hash,owner_id,state,created_at,last_active_at,expires_at) VALUES(?,?,?,?,?,?)',
                     (guest_sessions.digest(secrets.token_urlsafe(32)), owner, 'account', now, now, now + 10 * 365 * 86400))
        row = conn.execute('SELECT * FROM guest_sessions WHERE owner_id=?', (owner,)).fetchone()
    if not conn.execute('SELECT 1 FROM cs_customer WHERE account_id=?', (account_id,)).fetchone():
        conn.execute('INSERT OR IGNORE INTO cs_customer(id,tg_id,account_id) VALUES(?,?,?)',
                     (secrets.token_hex(6), owner, account_id))
    return row


def resolve(conn, account_id=None, visitor=''):
    if account_id:
        account_session(conn, account_id)
        return conn.execute('SELECT id FROM cs_customer WHERE account_id=?', (account_id,)).fetchone()[0]
    session = guest_sessions.validate(conn, visitor)
    row = conn.execute('SELECT id FROM cs_customer WHERE tg_id=?', (session['owner_id'],)).fetchone()
    return row[0] if row else None


def claim(conn, account_id: str, visitor: str) -> str:
    if not visitor:
        return resolve(conn, account_id=account_id)
    conn.execute('BEGIN IMMEDIATE')
    session = conn.execute('SELECT * FROM guest_sessions WHERE token_hash=?',
                           (guest_sessions.digest(visitor),)).fetchone()
    if not session:
        raise HTTPException(401, 'session_required')
    if session['state'] == 'merged':
        if session['claimed_account_id'] == account_id:
            return resolve(conn, account_id=account_id)
        raise HTTPException(410, 'session_expired')
    if session['state'] != 'active' or session['expires_at'] <= time.time():
        raise HTTPException(410, 'session_expired')
    current_session = conn.execute('SELECT pending_photo FROM guest_sessions WHERE owner_id=?',
                                   (owner_id(account_id),)).fetchone()
    if session['pending_photo'] and current_session and current_session['pending_photo']:
        raise HTTPException(409, 'account_pending_photo_requires_resolution')
    guest = conn.execute('SELECT * FROM cs_customer WHERE tg_id=?', (session['owner_id'],)).fetchone()
    account = conn.execute('SELECT * FROM cs_customer WHERE account_id=?', (account_id,)).fetchone()
    if guest and not account:
        conn.execute('UPDATE cs_customer SET tg_id=?,account_id=? WHERE id=?',
                     (owner_id(account_id), account_id, guest['id']))
        customer_id = guest['id']
        conn.execute("UPDATE note_batches SET owner_kind='user' WHERE owner_kind='guest' AND owner_id=?", (customer_id,))
    elif guest and account:
        customer_id = account['id']
        conn.execute("UPDATE cs_link SET expires_at='1970-01-01' WHERE customer_id=?", (guest['id'],))
        for table in ('cs_note', 'cs_conversation_log'):
            conn.execute(f'UPDATE {table} SET customer_id=? WHERE customer_id=?', (customer_id, guest['id']))
        for table in ('cs_card_info', 'cs_context', 'cs_photo_candidates'):
            conn.execute(f'UPDATE OR IGNORE {table} SET customer_id=? WHERE customer_id=?', (customer_id, guest['id']))
            conn.execute(f'DELETE FROM {table} WHERE customer_id=?', (guest['id'],))
        has_active = conn.execute("SELECT 1 FROM note_batches WHERE owner_kind='user' AND owner_id=? AND active=1", (customer_id,)).fetchone()
        if has_active:
            conn.execute("UPDATE note_batches SET active=0 WHERE owner_kind='guest' AND owner_id=?", (guest['id'],))
        conn.execute("UPDATE note_batches SET owner_kind='user',owner_id=? WHERE owner_kind='guest' AND owner_id=?", (customer_id, guest['id']))
        conn.execute('DELETE FROM cs_customer WHERE id=?', (guest['id'],))
    else:
        customer_id = resolve(conn, account_id=account_id)
    if guest:
        conn.execute("UPDATE cs_link SET expires_at='1970-01-01' WHERE customer_id=?", (customer_id,))
    conn.execute("UPDATE guest_sessions SET state='merged',claimed_account_id=?,pending_photo='' WHERE token_hash=?",
                 (account_id, session['token_hash']))
    account_row = account_session(conn, account_id)
    if session['pending_photo']:
        conn.execute("UPDATE guest_sessions SET pending_photo=?,photo_mode=? WHERE token_hash=?",
                     (session['pending_photo'], session['photo_mode'], account_row['token_hash']))
    elif session['photo_mode'] and not account_row['photo_mode']:
        conn.execute("UPDATE guest_sessions SET photo_mode=? WHERE token_hash=?", (session['photo_mode'], account_row['token_hash']))
    return customer_id
