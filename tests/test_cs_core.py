"""C端商家自定义红线；平台不播种默认红线，阶梯报价功能已移除。"""
import sqlite3

import pytest

from catalog import cs, db


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


def test_tier_functions_removed():
    assert not hasattr(cs,'parse_tiers') and not hasattr(cs,'pick_tier')


def test_platform_has_no_redline(conn):
    cs.set_redline(conn,None,'允许任意报价')
    assert '平台公共红线' not in cs.build_knowledge(conn)
    assert cs.SYSTEM_HARD_RULE == ''


# ---------- 判定②：红线知识 ----------

def test_store_redline_starts_empty(conn):
    """新库不启用任何平台或商家默认红线。"""
    red = cs.get_redline(conn)
    assert red['text_raw'] == ''
    assert red['text_summary'] == ''


def test_set_and_get_store_redline(conn):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    red = cs.get_redline(conn)
    assert red['text_raw'] == '数量少于50的转人工'
    assert red['text_summary'] == '数量少于50转人工'
    assert red['product_id'] is None


def test_product_redline_overrides_store(conn):
    cs.set_redline(conn, None, '店级：低于20转人工', '店级：低于20转人工')
    cs.set_redline(conn, 'p1', '这款低于50个不接、低于9块不谈', '低于50不接；低于9块不谈')
    merged = cs.get_redline(conn, 'p1')
    assert '50' in merged['text_raw']                     # 商品级覆盖
    store = cs.get_redline(conn, 'p2')                    # 未设商品 → 继承店级
    assert store['text_raw'] == '店级：低于20转人工'


def test_set_redline_updates_in_place(conn):
    cs.set_redline(conn, None, 'v1', 'v1')
    cs.set_redline(conn, None, 'v2', 'v2')
    assert cs.get_redline(conn)['text_raw'] == 'v2'       # UNIQUE(product_id) 覆盖式
    rows = conn.execute("SELECT * FROM cs_redline WHERE product_id=''").fetchall()
    assert len(rows) == 1


def test_summarize_passthrough_and_compress(conn):
    """总结版：短原文直录；长原文由 LLM 压缩（测试里 mock）。"""
    assert cs.summarize('短红线') == '短红线'
    long_text = ' '.join(f'规则{i}' for i in range(200))   # >500字
    cs._llm_summarize = lambda text: '压缩版'
    assert cs.summarize(long_text, llm=True) == '压缩版'


def test_product_columns_migrated(conn):
    """老库补列：tier_price / cs_visible。"""
    for tbl in ('product_razor', 'product_curler'):
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
        assert 'tier_price' in cols and 'cs_visible' in cols, tbl


def test_cs_customer_note_tables(conn):
    conn.execute("INSERT INTO cs_customer(id, tg_id, tg_name) VALUES('c1','12345','测试买家')")
    conn.execute("INSERT INTO cs_note(customer_id, photo, fields_json, status) "
                 "VALUES('c1','img/x.jpg','{\"价格\":\"1.5\"}','draft')")
    conn.commit()
    row = conn.execute('SELECT * FROM cs_note WHERE customer_id=?', ('c1',)).fetchone()
    assert row['fields_json'].startswith('{') and row['status'] == 'draft'


# ---------- 知识组装（prompt 注入） ----------

def test_build_knowledge_injection(conn):
    cs.set_redline(conn, None, '店级红线原文', '店级红线总结')
    cs.set_redline(conn, 'p1', '商品红线原文', '商品红线总结')
    k = cs.build_knowledge(conn, ['p1'])
    assert '店级红线总结' in k and '商品红线总结' in k
    k2 = cs.build_knowledge(conn, ['p_other'])
    assert '店级红线总结' in k2 and '商品红线总结' not in k2
    assert '系统级' not in k2
