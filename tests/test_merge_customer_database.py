import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from catalog import db, shop_link
from scripts.merge_customer_database import merge


def test_merge_keeps_current_merchant_data_and_customer_identity(tmp_path):
    merchant, customer, output = (tmp_path / n for n in ('merchant.db', 'customer.db', 'merged.db'))
    old = sqlite3.connect(merchant)
    schema = Path(db._SCHEMA).read_text().split('-- ===== C端')[0]
    old.executescript(schema)
    old.execute("INSERT INTO product_curler(id,inner_code,item_no,price) VALUES('p','LIVE','LATEST-MODEL','42')")
    old.commit(); old.close()
    original_hash = hashlib.sha256(merchant.read_bytes()).hexdigest()
    c = db.connect(str(customer)); db.init_db(c)
    identity = shop_link.profile(c)['shop_id']
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,price) VALUES('p','OLD','STALE-MODEL','7')")
    c.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer','123')")
    c.execute("INSERT INTO cs_note(customer_id,photo,fields_json,source_shop_id) VALUES('buyer','/old/photos/x.jpg','{}',?)", (identity,))
    c.execute("INSERT INTO cs_inbox(update_id,payload,processed) VALUES(876,'{}',1)")
    c.commit(); c.close()
    counts = merge(merchant, customer, output, '/old/photos', '/shared/photos')
    assert counts['product_curler'] == counts['cs_note'] == 1
    result = db.connect(str(output))
    p = result.execute('SELECT * FROM product_curler').fetchone()
    assert (p['item_no'], p['price'], p['shop_id']) == ('LATEST-MODEL', '42', identity)
    assert result.execute('SELECT photo FROM cs_note').fetchone()[0] == '/shared/photos/x.jpg'
    assert result.execute('SELECT max(update_id) FROM cs_inbox').fetchone()[0] == 876
    assert hashlib.sha256(merchant.read_bytes()).hexdigest() == original_hash
    with pytest.raises(ValueError):
        merge(merchant, customer, output, '/old/photos', '/shared/photos')
    result.close()
