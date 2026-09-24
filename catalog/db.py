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
    for field in ('mode', 'category_key', 'content_sha256', 'phase',
                  'template_doc_id', 'template_keys_json'):
        if field not in cols:
            if field == 'template_doc_id':
                conn.execute("ALTER TABLE import_doc ADD COLUMN template_doc_id INTEGER")
            elif field == 'template_keys_json':
                conn.execute("ALTER TABLE import_doc ADD COLUMN template_keys_json TEXT NOT NULL DEFAULT '[]'")
            elif field == 'phase':
                conn.execute("ALTER TABLE import_doc ADD COLUMN phase TEXT NOT NULL DEFAULT 'legacy'")
            else:
                conn.execute(f"ALTER TABLE import_doc ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE import_doc SET source_key=filename WHERE source_key=''")
    inbox_cols = {r[1] for r in conn.execute('PRAGMA table_info(cs_inbox)')}
    if 'next_attempt_at' not in inbox_cols:
        conn.execute("ALTER TABLE cs_inbox ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT '1970-01-01'")
    cat_cols = {r[1] for r in conn.execute('PRAGMA table_info(category_template)')}
    if cat_cols and 'supplier' not in cat_cols:
        conn.execute("ALTER TABLE category_template ADD COLUMN supplier TEXT NOT NULL DEFAULT ''")
    shop_cols = {r[1] for r in conn.execute('PRAGMA table_info(shop_profile)')}
    for field in ('address', 'business_hours', 'shipping_info', 'faq'):
        if field not in shop_cols:
            conn.execute(f"ALTER TABLE shop_profile ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    cust_cols = {r[1] for r in conn.execute('PRAGMA table_info(cs_customer)')}
    if cust_cols and 'lang' not in cust_cols:
        conn.execute("ALTER TABLE cs_customer ADD COLUMN lang TEXT NOT NULL DEFAULT ''")
    from .templates import TEMPLATES
    for t in TEMPLATES.values():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({t.table})')}
        if cols and 'remark' not in cols:
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN remark TEXT DEFAULT ''")
        if cols and 'tier_price' not in cols:      # 历史兼容列：已停用，应用禁止读写和报价
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN tier_price TEXT")
        if cols and 'cs_visible' not in cols:      # C端：对客户可见（默认关）
            conn.execute(f"ALTER TABLE {t.table} ADD COLUMN cs_visible INTEGER DEFAULT 0")
    # 动态分类报价映射：老库补列并按表头回填推荐值（价格列唯一才自动绑，
    # 多候选留给商家显式指定——不能猜价格口径）。
    import json as _json
    tpl_cols = {r[1] for r in conn.execute('PRAGMA table_info(category_template)')}
    if tpl_cols and 'quote_map_json' not in tpl_cols:
        conn.execute("ALTER TABLE category_template ADD COLUMN quote_map_json TEXT NOT NULL DEFAULT ''")
        from . import dynamic_catalog
        for row in conn.execute(
                "SELECT key, fields_json FROM category_template WHERE storage='dynamic'").fetchall():
            try:
                fields = _json.loads(row['fields_json'])
            except (TypeError, ValueError):
                continue
            suggested = dynamic_catalog.suggest_quote_map(fields)
            if suggested:
                conn.execute('UPDATE category_template SET quote_map_json=? WHERE key=?',
                             (_json.dumps(suggested, ensure_ascii=False), row['key']))


def _seed_cs(conn):
    """红线完全由商家提交并审批；平台不播种任何默认规则。"""
    return None
