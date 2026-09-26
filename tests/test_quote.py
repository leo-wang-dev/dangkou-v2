"""报价单：动态分类夹具下的 v2.2 模板出单（v1 五列模板与异步 job 已随固定品类删除）。"""
import base64
import sqlite3

import openpyxl
import pytest

from catalog import db, quote
from catalog.storage import LocalStorage
from tests.conftest import seed_products


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


@pytest.fixture()
def seeded(tmp_path):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    seed_products(conn, [
        {'id': 'p1', 'inner_code': 'KS-AAAAAAAA',
         'data': {'model': '8226', 'price': '21.5', 'ctn': 'QTY：40 PCS\nMEAS：60*40*40 CM'},
         'images': [st.save('test_cat', 'p1', 'main.png', PNG)]},
        {'id': 'p2', 'inner_code': 'KS-BBBBBBBB',
         'data': {'model': '8227', 'price': '30',
                  'ctn': 'QTY：50 PCS\nN.W.：10 KGS\nG.W.：11 KGS\nMEAS：52*42*46 CM'}},
    ])
    return conn, st, str(tmp_path)


def test_multi_item_quote_with_adjustment(seeded):
    """多商品报价（v2.2 ELETRO BELEZA 模板）：各自数量 + 百分比调整 + 小计 + 合计。"""
    conn, st, base = seeded
    from catalog import quote as q
    out = base + '/multi.xlsx'
    q.generate_v2(conn, st, [
        {'category': 'test_cat', 'product_id': 'p1', 'quantity': 100},
        {'category': 'test_cat', 'product_id': 'p2', 'quantity': 500},
    ], price_adjustment_pct=3, out_path=out)
    ws = openpyxl.load_workbook(out).active
    # 表头（17行：F=QUANTITY G=TOTAL AMOUNT）
    assert ws.cell(17, 6).value == 'QUANTITY'
    assert ws.cell(17, 7).value == 'TOTAL AMOUNT'
    # 数据
    assert ws.cell(18, 1).value == '8226'          # ITEM NO.
    assert ws.cell(18, 5).value == 22              # 21.5*1.03=22.145 round=22
    assert ws.cell(18, 6).value == 120             # QUANTITY=整箱(40×3)
    assert ws.cell(18, 7).value == 22 * 120                # G 小计=值（整箱120）
    assert ws.cell(19, 1).value == '8227'
    assert ws.cell(19, 5).value == 31              # 30*1.03=30.9 round=31
    assert ws.cell(19, 6).value == 500
    assert ws.cell(19, 7).value == 31 * 500
    # 合计（k=2 删4空行 → TOTAL 在 18+2+3=23）
    assert ws.cell(23, 1).value == 'TOTAL'
    total = 22 * 120 + 31 * 500
    assert ws.cell(23, 7).value == total
    assert ws.cell(24, 7).value == round(total * 0.3, 2)  # 默认定金30%
    assert ws.cell(25, 7).value == total - round(total * 0.3, 2)
