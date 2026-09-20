"""Optimistic source snapshot, checked under the approval writer transaction."""
import hashlib
import json
from .templates import TEMPLATES


def rows(conn, category, source_key):
    return conn.execute(
        f'SELECT p.* FROM {TEMPLATES[category].table} p JOIN import_doc d ON p.source_doc=d.id '
        'WHERE d.source_key=? ORDER BY p.id', (source_key,)).fetchall()


def digest(records):
    return hashlib.sha256(json.dumps([dict(r) for r in records], sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def current(conn, category, source_key):
    return digest(rows(conn, category, source_key))
