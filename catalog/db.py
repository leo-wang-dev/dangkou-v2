import os
import sqlite3

_SCHEMA = os.path.join(os.path.dirname(__file__), 'schema.sql')


def connect(path=None):
    from . import config
    p = path or config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)  # 后台导入线程共用
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def init_db(conn):
    conn.executescript(open(_SCHEMA, encoding='utf-8').read())
    _migrate(conn)
    from . import shop_link
    shop_link.migrate(conn)
    from . import dynamic_catalog
    dynamic_catalog.seed_legacy_templates(conn)
    _seed_cs(conn)
    conn.commit()


def _migrate(conn):
    """老库补列（CREATE TABLE IF NOT EXISTS 不会给已存在的表加新列）。"""
    cols = {r[1] for r in conn.execute('PRAGMA table_info(import_doc)')}
    if 'source_key' not in cols:
        conn.execute("ALTER TABLE import_doc ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE import_doc SET source_key=filename WHERE source_key=''")
    inbox_cols = {r[1] for r in conn.execute('PRAGMA table_info(cs_inbox)')}
    if 'next_attempt_at' not in inbox_cols:
        conn.execute("ALTER TABLE cs_inbox ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT '1970-01-01'")
    shop_cols = {r[1] for r in conn.execute('PRAGMA table_info(shop_profile)')}
    for field in ('address', 'business_hours', 'shipping_info', 'faq'):
        if field not in shop_cols:
            conn.execute(f"ALTER TABLE shop_profile ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    from .templates import TEMPLATES
    for t in TEMPLATES.values():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({t.table})')}
        if cols and 'remark' not in cols:
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN remark TEXT DEFAULT ''")
        if cols and 'tier_price' not in cols:      # 历史兼容列：已停用，应用禁止读写和报价
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN tier_price TEXT")
        if cols and 'cs_visible' not in cols:      # C端：对客户可见（默认关）
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN cs_visible INTEGER DEFAULT 0")


def _seed_cs(conn):
    """红线完全由商家提交并审批；平台不播种任何默认规则。"""
    return None
