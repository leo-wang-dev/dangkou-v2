import sqlite3

from catalog import db


def test_init_db_creates_category_tables_with_template_columns():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    for table in ('product_razor', 'product_curler', 'embedding',
                  'approval_ticket', 'import_doc'):
        names = [r['name'] for r in conn.execute(f'PRAGMA table_info({table})')]
        assert names, f'{table} 缺失'
    razor = [r['name'] for r in conn.execute('PRAGMA table_info(product_razor)')]
    assert 'model_no' in razor and 'giftbox_mm' in razor and 'inner_code' in razor
    curler = [r['name'] for r in conn.execute('PRAGMA table_info(product_curler)')]
    assert 'item_no' in curler and 'heater' in curler and 'frequency' in curler
