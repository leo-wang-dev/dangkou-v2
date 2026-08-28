import sqlite3
import struct

import pytest

from catalog import db, search


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


def _add(conn, pid, model, ivec):
    conn.execute("INSERT INTO product_razor(id, inner_code, model_no, image_main) "
                 "VALUES(?,?,?,?)", (pid, f'KS-{pid.upper():->8}', model, f'{pid}.png'))
    conn.execute("INSERT INTO embedding(product_id, category, image_path, vec) "
                 "VALUES(?,?,?,?)", (pid, 'razor', f'{pid}.png',
                                     struct.pack('1024f', *ivec)))
    conn.commit()


def test_query_ranks_by_cosine_and_excludes(conn):
    q = [1.0] + [0.0] * 1023
    _add(conn, 'p1', 'A', [1.0] + [0.0] * 1023)
    _add(conn, 'p2', 'B', [0.7] + [0.7141421] + [0.0] * 1022)
    r = search.query(conn, q, category='razor', top_k=5, exclude=[])
    assert r[0]['product_id'] == 'p1' and r[1]['product_id'] == 'p2'
    assert 0 < r[1]['score'] < r[0]['score'] <= 1.0
    r2 = search.query(conn, q, top_k=5, exclude=['p1'])
    assert r2[0]['product_id'] == 'p2'


def test_query_skips_delisted(conn):
    q = [1.0] + [0.0] * 1023
    _add(conn, 'p1', 'A', [1.0] + [0.0] * 1023)
    conn.execute("UPDATE product_razor SET status='delisted' WHERE id='p1'")
    conn.commit()
    assert search.query(conn, q) == []


def test_reindex_embeds_missing(conn, tmp_path, monkeypatch):
    from catalog.storage import LocalStorage
    st = LocalStorage(str(tmp_path))
    st.save('razor', 'p9', 'main.png', b'IMG')
    conn.execute("INSERT INTO product_razor(id, inner_code, model_no, image_main) "
                 "VALUES('p9','KS-'||'A'*8,'8225','razor/p9/main.png')")
    conn.commit()
    monkeypatch.setattr(search, 'embed_image', lambda b: [0.5] * 1024)
    n = search.reindex(conn, st, 'razor')
    assert n == 1
    assert conn.execute('SELECT COUNT(*) c FROM embedding').fetchone()['c'] == 1
    assert search.reindex(conn, st, 'razor') == 0  # 幂等
