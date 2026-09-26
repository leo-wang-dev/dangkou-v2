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
    old.execute("INSERT INTO category_template(key,name,version,fields_json,status,storage,source_sheet) "
                "VALUES('old_cat','旧库品类',1,'[]','approved','dynamic','旧库品类')")
    old.execute("INSERT INTO product_dynamic(id,category_key,inner_code,data_json) "
                "VALUES('p','old_cat','LIVE','{\"model\":\"LATEST-MODEL\"}')")
    old.commit(); old.close()
    original_hash = hashlib.sha256(merchant.read_bytes()).hexdigest()
    c = db.connect(str(customer)); db.init_db(c)
    identity = shop_link.profile(c)['shop_id']
    c.execute("INSERT INTO product_dynamic(id,category_key,inner_code,data_json) "
              "VALUES('p','stale_cat','OLD','{\"model\":\"STALE-MODEL\"}')")
    c.execute("INSERT INTO cs_customer(id,tg_id) VALUES('buyer','123')")
    c.execute("INSERT INTO cs_note(customer_id,photo,fields_json,source_shop_id) VALUES('buyer','/old/photos/x.jpg','{}',?)", (identity,))
    c.execute("INSERT INTO cs_inbox(update_id,payload,processed) VALUES(876,'{}',1)")
    c.commit(); c.close()
    counts = merge(merchant, customer, output, '/old/photos', '/shared/photos')
    assert counts['product_dynamic'] == counts['cs_note'] == 1
    result = db.connect(str(output))
    p = result.execute('SELECT * FROM product_dynamic').fetchone()
    assert json.loads(p['data_json'])['model'] == 'LATEST-MODEL'    # 商家库为权威
    assert p['shop_id'] == identity
    assert result.execute('SELECT photo FROM cs_note').fetchone()[0] == '/shared/photos/x.jpg'
    assert result.execute('SELECT max(update_id) FROM cs_inbox').fetchone()[0] == 876
    assert hashlib.sha256(merchant.read_bytes()).hexdigest() == original_hash
    with pytest.raises(ValueError):
        merge(merchant, customer, output, '/old/photos', '/shared/photos')
    result.close()
