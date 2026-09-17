"""C端核心：阶梯价结构化取档 + 红线知识存取合并（v1.2 两个判定的落地）。

判定①：阶梯价=结构化档位表，代码取档计算，AI 不做算术。
判定②：红线=自然语言知识（店级+商品级覆盖），存原文+总结版。
"""
import sqlite3

import pytest

from catalog import cs, db


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


# ---------- 判定①：阶梯价 ----------

def test_parse_tiers_basic_and_order():
    assert cs.parse_tiers('20:12;50:11;100:10.5') == [(20, 12.0), (50, 11.0), (100, 10.5)]
    assert cs.parse_tiers('100:10.5; 20:12; 50:11') == [(20, 12.0), (50, 11.0), (100, 10.5)]  # 自动升序


def test_parse_tiers_dirty_input():
    assert cs.parse_tiers('20个12元；50个11元') == [(20, 12.0), (50, 11.0)]      # 口语变体
    assert cs.parse_tiers('') == []
    assert cs.parse_tiers(None) == []
    assert cs.parse_tiers('随便写的') == []


def test_parse_tiers_single():
    assert cs.parse_tiers('20:12') == [(20, 12.0)]


def test_pick_tier_by_quantity():
    tiers = cs.parse_tiers('20:12;50:11;100:10.5')
    assert cs.pick_tier(tiers, 20) == 12.0        # 恰在档位线
    assert cs.pick_tier(tiers, 49) == 12.0
    assert cs.pick_tier(tiers, 50) == 11.0
    assert cs.pick_tier(tiers, 999) == 10.5
    assert cs.pick_tier(tiers, 19) is None        # 低于最低档 → 转人工（None）


def test_pick_tier_empty():
    assert cs.pick_tier([], 100) is None


# ---------- 判定②：红线知识 ----------

def test_store_redline_seeded_by_default(conn):
    """init 后店级红线=默认预置文案（开箱即用）。"""
    red = cs.get_redline(conn)
    assert '20' in red['text_raw'] and '1.2' in red['text_raw']


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
    assert '军火' in cs.SYSTEM_HARD_RULE                         # 系统级硬规则常驻
