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
    conn.commit()
