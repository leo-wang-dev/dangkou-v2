"""One-time cutover: retain merchant tables and import customer state into a new file.

Stop both writers before the final merge. Inputs are opened read-only and the
output must not already exist. Never use a stale merchant snapshot as authority.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import db


def merge(merchant, customer, output, old_photos, new_photos):
    merchant, customer, output = map(lambda p: Path(p).resolve(), (merchant, customer, output))
    if output.exists() or output in (merchant, customer) or merchant == customer:
        raise ValueError('Refusing to overwrite or merge the same database')
    output.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(merchant.as_uri() + '?mode=ro', uri=True)
    source_customer = sqlite3.connect(customer.as_uri() + '?mode=ro', uri=True)
    out = sqlite3.connect(output)
    try:
        src.backup(out)
        names = [r[0] for r in source_customer.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'cs_%' OR name='shop_profile')")]
        names.sort(key=lambda n: (n != 'shop_profile', n))
        for name in names:
            assert name.replace('_', '').isalnum()
            if out.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone():
                raise ValueError('Merchant database already has customer tables; manual reconciliation required')
            sql = source_customer.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
            out.execute(sql)
            rows = source_customer.execute('SELECT * FROM ' + name).fetchall()
            if rows:
                out.executemany('INSERT INTO ' + name + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
        out.execute('UPDATE cs_note SET photo=replace(photo,?,?)', (old_photos, new_photos))
        out.execute('UPDATE cs_outbox SET body=replace(body,?,?)', (old_photos, new_photos))
        out.commit()
        out.close()
        out = db.connect(str(output))
        db.init_db(out)
        assert out.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not out.execute('PRAGMA foreign_key_check').fetchall()
        return {t: out.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                for t in ('product_razor', 'product_curler', 'cs_customer', 'cs_note', 'cs_inbox')}
    finally:
        out.close()
        src.close()
        source_customer.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('merchant', 'customer', 'output', 'old-photos', 'new-photos'):
        p.add_argument('--' + key, required=True)
    args = vars(p.parse_args())
    print(json.dumps(merge(**args), ensure_ascii=False))
