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
    _seed_cs(conn)
    conn.commit()


def _migrate(conn):
    """老库补列（CREATE TABLE IF NOT EXISTS 不会给已存在的表加新列）。"""
    from .templates import TEMPLATES
    for t in TEMPLATES.values():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({t.table})')}
        if cols and 'remark' not in cols:
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN remark TEXT DEFAULT ''")
        if cols and 'tier_price' not in cols:      # C端：阶梯价（结构化档位表）
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN tier_price TEXT")
        if cols and 'cs_visible' not in cols:      # C端：对客户可见（默认关）
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN cs_visible INTEGER DEFAULT 0")


def _seed_cs(conn):
    """店级红线播种默认文案（开箱即用，商家可整体改写）。"""
    from . import cs as _cs
    row = conn.execute("SELECT 1 FROM cs_redline WHERE product_id=''").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO cs_redline(product_id, text_raw, text_summary) VALUES('',?,?)",
            (_cs.DEFAULT_STORE_REDLINE, _cs.DEFAULT_STORE_REDLINE))
