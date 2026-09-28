"""Atomic OTP lifecycle. Delivery is outside transactions; only latest delivered code works."""
import hashlib
import hmac
import os
import secrets
import time
from fastapi import HTTPException


def setting(name, default, minimum=1):
    return max(minimum, int(os.environ.get(name, default)))


def migrate(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(auth_codes)')}
    for name, definition in [('sent_at','REAL NOT NULL DEFAULT 0'),
                             ('delivery','TEXT NOT NULL DEFAULT \'invalid\''),
                             ('attempts','INTEGER NOT NULL DEFAULT 0')]:
        if name not in columns:
            conn.execute(f'ALTER TABLE auth_codes ADD COLUMN {name} {definition}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_email ON auth_codes(email)')


def issue(conn, email, deliver):
    now = time.time()
    ttl = setting('USER_APP_OTP_TTL_SECONDS',600)
    cooldown = setting('USER_APP_OTP_COOLDOWN_SECONDS',60,0)
    code = f'{secrets.randbelow(1000000):06d}'
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('SELECT *,rowid FROM auth_codes WHERE email=? ORDER BY rowid DESC LIMIT 1',(email,)).fetchone()
    if row and (row['sent_at'] + cooldown > now or
                (row['delivery']=='pending' and row['sent_at'] + ttl > now)):
        conn.rollback()
        raise HTTPException(429,'otpCooldown')
    recent = {r[0] for r in conn.execute("SELECT code_hash FROM auth_codes WHERE email=? AND datetime(expires_at)>datetime('now')",(email,))}
    while hashlib.sha256(f'{email}:{code}'.encode()).hexdigest() in recent:
        code = f'{secrets.randbelow(1000000):06d}'
    conn.execute("UPDATE auth_codes SET delivery='invalid' WHERE email=?",(email,))
    identity = conn.execute("INSERT INTO auth_codes(email,code_hash,expires_at,sent_at,delivery) VALUES(?,?,datetime('now',?),?,'pending')",
        (email,hashlib.sha256(f'{email}:{code}'.encode()).hexdigest(),f'+{ttl} seconds',now)).lastrowid
    conn.commit()
    try:
        deliver(code, ttl)
    except Exception:
        conn.execute("UPDATE auth_codes SET delivery='failed' WHERE rowid=? AND delivery='pending'",(identity,))
        conn.commit()
        raise HTTPException(503,'otpDeliveryFailed') from None
    conn.execute("UPDATE auth_codes SET delivery='delivered' WHERE rowid=? AND delivery='pending'",(identity,))
    conn.commit()


def consume(conn,email,code):
    """Leave success transaction open for atomic account/guest merge. Failures commit attempts."""
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute("SELECT *,rowid,datetime(expires_at)>datetime('now') AS fresh FROM auth_codes WHERE email=? ORDER BY rowid DESC LIMIT 1",(email,)).fetchone()
    valid = row and row['delivery']=='delivered' and not row['used_at'] and row['fresh'] and row['attempts'] < setting('USER_APP_OTP_MAX_ATTEMPTS',5)
    if valid:
        conn.execute('UPDATE auth_codes SET attempts=attempts+1 WHERE rowid=?',(row['rowid'],))
        valid = hmac.compare_digest(row['code_hash'],hashlib.sha256(f'{email}:{code}'.encode()).hexdigest())
    if not valid:
        conn.commit()
        raise HTTPException(400,'验证码错误或已过期')
    conn.execute("UPDATE auth_codes SET used_at=datetime('now') WHERE rowid=?",(row['rowid'],))
