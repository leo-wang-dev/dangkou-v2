"""Verify central buyer bearer tokens before a merchant DB writer transaction."""
import os
import re

import httpx
from fastapi import HTTPException


def has_bearer(request):
    return request.headers.get('authorization', '').lower().startswith('bearer ')


def verify(request) -> str:
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('bearer ') or not auth[7:].strip():
        raise HTTPException(401, 'login_required')
    base = os.environ.get('CUSTOMER_IDENTITY_BASE_URL', '').strip().rstrip('/')
    if not base or not base.startswith(('http://', 'https://')):
        raise HTTPException(503, 'identity_service_unavailable')
    try:
        response = httpx.get(base + '/me', headers={'Authorization': auth}, timeout=3, trust_env=False)
    except httpx.HTTPError as exc:
        raise HTTPException(503, 'identity_service_unavailable') from exc
    if response.status_code == 401:
        raise HTTPException(401, 'login_expired')
    if response.status_code != 200:
        raise HTTPException(503, 'identity_service_unavailable')
    try:
        identity = response.json()
        account_id = identity['account_id']
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, 'identity_service_unavailable') from None
    if identity.get('kind') != 'user' or not isinstance(account_id, str) or not re.fullmatch(r'[0-9a-f]{32}', account_id):
        raise HTTPException(503, 'identity_service_unavailable')
    aliases = identity.get('account_aliases', [])
    if (not isinstance(aliases, list) or len(aliases) > 32 or
            any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value)
                for value in aliases)):
        raise HTTPException(503, 'identity_service_unavailable')
    request.state.account_aliases = list(dict.fromkeys(value for value in aliases if value != account_id))
    return account_id
