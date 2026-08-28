import sqlite3

import openpyxl
import pytest

from catalog import db, quote
from catalog.storage import LocalStorage


import base64
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


@pytest.fixture()
def seeded(tmp_path):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    rel = st.save('curler', 'p1', 'main.png', PNG)
    conn.execute(
        "INSERT INTO product_curler(id, inner_code, item_no, ctn_qty, price, "
        "voltage, power, material, image_main) VALUES(?,?,?,?,?,?,?,?,?)",
        ('p1', 'KS-AAAAAAAA', '8226', '40', '21.5', '110-240', '44w', 'PBT', rel))
    conn.commit()
    return conn, st, str(tmp_path)


def test_quote_five_cols_and_rows(seeded):
    conn, st, base = seeded
    out = base + '/q.xlsx'
    quote.generate(conn, st, 'curler', ['p1'], out)
    wb = openpyxl.load_workbook(out)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert headers[:5] == ['型号', '图片', '价格', '产品规格', '起订量']
    assert ws['A2'].value == '8226' and ws['C2'].value == '21.5'
    assert ws['E2'].value == '40'                       # 卷发棒：起订量=装箱数量
    assert '110-240' in ws['D2'].value                  # 规格拼串含电压
    assert len(ws._images) >= 1                         # 图片已嵌入


def test_quote_razor_moq_from_ctn_spec(seeded):
    conn, st, base = seeded
    conn.execute(
        "INSERT INTO product_razor(id, inner_code, model_no, ctn_spec, price, "
        "size_mm, color, image_main) VALUES(?,?,?,?,?,?,?,?)",
        ('r1', 'KS-BBBBBBBB', 'S1', '40pcs/箱', '9.9', '120x60', '黑', ''))
    conn.commit()
    out = base + '/q2.xlsx'
    quote.generate(conn, st, 'razor', ['r1'], out)
    ws = openpyxl.load_workbook(out).active
    assert ws['E2'].value == '40'                       # 剃须刀：从箱规文本取数量
    assert '120x60' in ws['D2'].value
