"""Central mini-program identity: exchange codes server-side and bind accounts."""
from __future__ import annotations

import hashlib
import os

import requests
from fastapi import HTTPException


SHADOW_DOMAIN = '@wechat.invalid'


def is_shadow(email: str) -> bool:
    return str(email or '').endswith(SHADOW_DOMAIN)


def shadow_email(appid: str, openid: str) -> str:
    digest = hashlib.sha256((appid + '\0' + openid).encode()).hexdigest()
    return 'wx_' + digest + SHADOW_DOMAIN


def migrate(conn) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS wechat_identities(
        appid TEXT NOT NULL,
        openid TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY(appid, openid))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_wechat_email ON wechat_identities(email)')


def exchange_code(code: str) -> tuple[str, str]:
    """Only this server sees the AppSecret; never return session_key to a client."""
    appid = os.environ.get('WECHAT_MINIAPP_APP_ID', '').strip()
    secret = os.environ.get('WECHAT_MINIAPP_APP_SECRET', '').strip()
    if not appid or not secret:
        raise HTTPException(503, '微信小程序登录尚未配置')
    try:
        response = requests.get('https://api.weixin.qq.com/sns/jscode2session',
            params={'appid': appid, 'secret': secret, 'js_code': code,
                    'grant_type': 'authorization_code'}, timeout=8)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(503, '微信登录服务暂不可用，请重试') from None
    if not isinstance(data, dict) or data.get('errcode') or not data.get('openid'):
        raise HTTPException(401, '微信登录凭证无效，请重试')
    openid = str(data['openid'])
    if len(openid) > 128:
        raise HTTPException(401, '微信登录凭证无效，请重试')
    return appid, openid


def bind_or_create(conn, appid: str, openid: str, current_email: str = '') -> str:
    """Return account email; an already-bound identity cannot change owners."""
    if not appid or not openid or len(appid) > 128 or len(openid) > 128:
        raise HTTPException(401, '微信登录凭证无效，请重试')
    row = conn.execute('SELECT email FROM wechat_identities WHERE appid=? AND openid=?',
                       (appid, openid)).fetchone()
    bound = row['email'] if row else ''
    if current_email and is_shadow(current_email) and current_email != bound:
        raise HTTPException(409, '请先绑定邮箱，不能合并两个微信身份')
    if bound and current_email and bound != current_email:
        raise HTTPException(409, '该微信已绑定其他账号')
    email = current_email or bound or shadow_email(appid, openid)
    if not conn.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
        import secrets
        conn.execute('INSERT INTO users(email,account_id) VALUES(?,?)',
                     (email, secrets.token_hex(16)))
    if not bound:
        conn.execute('INSERT INTO wechat_identities(appid,openid,email) VALUES(?,?,?)',
                     (appid, openid, email))
    return email


def link_email(conn, source_email: str, target_email: str) -> None:
    """An OTP-verified email absorbs the current WeChat-only account."""
    if not is_shadow(source_email) or not conn.execute(
            'SELECT 1 FROM wechat_identities WHERE email=?', (source_email,)).fetchone():
        raise HTTPException(409, '当前账号不是待绑定的微信账号')
    if source_email == target_email:
        return
    conn.execute("UPDATE notes SET owner_id=? WHERE owner_kind='user' AND owner_id=?",
                 (target_email, source_email))
    if conn.execute("SELECT 1 FROM note_batches WHERE owner_kind='user' AND owner_id=? AND active=1",
                    (source_email,)).fetchone():
        conn.execute("UPDATE note_batches SET active=0 WHERE owner_kind='user' AND owner_id=?",
                     (target_email,))
    conn.execute("UPDATE note_batches SET owner_id=? WHERE owner_kind='user' AND owner_id=?",
                 (target_email, source_email))
    conn.execute('UPDATE wechat_identities SET email=? WHERE email=?',
                 (target_email, source_email))
    conn.execute('UPDATE session_tokens SET email=? WHERE email=?',
                 (target_email, source_email))
    conn.execute('DELETE FROM users WHERE email=?', (source_email,))
