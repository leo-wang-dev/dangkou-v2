"""报价单（纯代码生成）：动态分类夹具下的列头/数据行/合计/定金/尾款全链。

验收口径（老板 2026-09-27 拍板，弃模板文件）：
- 第1行=14列头（沿用 v2.2 英文列名，加粗+底色），数据行从第2行起，PHOTO 嵌商品主图；
- 数据区后三行 TOTAL/DEPOSIT/BALANCE——写值不写公式（手机/WPS 预览不重算公式也可见）；
- 取数/计算链沿用 v2.2：quote_map 映射、加点位、整箱取整、箱数/毛重/体积；
- quote_map 未配置（缺型号列/价格列）明确报错，不猜口径。
"""
import base64
import math
import os
import shutil
import sqlite3

import openpyxl
import pytest

from catalog import db, quote
from catalog.storage import LocalStorage
from tests.conftest import seed_products

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

HEADERS = ['型号或品名','商品照片','描述','颜色','单价','数量','金额','装箱数','箱数','每箱毛重','每箱净重','箱体尺寸','总毛重','总体积 (m³)']

# ---- 生产库 24 种真实箱规变体（2026-09-03 从 catalog.db 全量拉取）----
REAL_CTN = [
    ('QTY：40 PCS\nN.W.：16.9 KGS\nG.W.：17.7 KGS\nMEAS：38.5*37.5*42.5 CM', 40, 16.9, 17.7, (38.5, 37.5, 42.5)),
    ('QTY：60 PCS\nN.W.：18 KGS\nG.W.：19 KGS\nMEAS：47*45.5*42.5 CM', 60, 18, 19, (47, 45.5, 42.5)),
    ('QTY:   80 PCS\nN.W.:  14.3  KGS       \nG.W.:  15.1  KGS         \nMEAS:39.5X28X44.5CM', 80, 14.3, 15.1, (39.5, 28, 44.5)),
    ('QTY: 40PCS\nN.W.: 14.8KGS\nG.W.:15.5KGS\nMEAS:37.5*29*40.5CM', 40, 14.8, 15.5, (37.5, 29, 40.5)),
    ('QTY：40 PCS\nN.W.：13.7 KGS\nG.W.：14.5 KGS\nMEAS：38.5*37.5*42.5 CM', 40, 13.7, 14.5, (38.5, 37.5, 42.5)),
    ('QTY：40 PCS\nN.W.：12.4 KGS\nG.W.：13.3 KGS\nMEAS：43*41.5*45  CM', 40, 12.4, 13.3, (43, 41.5, 45)),
    ('QTY：60 PCS\nN.W.：11.9 KGS\nG.W.：12.5 KGS\nMEAS： 42X32X33  CM', 60, 11.9, 12.5, (42, 32, 33)),
    ('QTY：60 PCS\nN.W.：16.2 KGS\nG.W.：17.0 KGS\nMEAS：41*40.5*42cm', 60, 16.2, 17.0, (41, 40.5, 42)),
    ('QTY: 40 PCS\nN.W.:12.2KGS\nG.W.:13.2KGS\nMEAS:48.5X44.5X25.5 CM', 40, 12.2, 13.2, (48.5, 44.5, 25.5)),
    ('QTY: 60 PCS\nN.W.:16.2KGS\nG.W.:17.3KGS\nMEAS:48.5X44.5X37 CM', 60, 16.2, 17.3, (48.5, 44.5, 37)),
    ('QTY：20 PCS\nN.W.：11 KGS\nG.W.：11.7 KGS\nMEAS：37*30*45.5 CM', 20, 11, 11.7, (37, 30, 45.5)),
    ('QTY：60 PCS\nN.W.：17.0 KGS\nG.W.：17.9 KGS\nMEAS：45*39*41.5 CM\n（单机）', 60, 17.0, 17.9, (45, 39, 41.5)),
    ('QTY：40 PCS\nN.W.：11.9 KGS\nG.W.：12..9 KGS\nMEAS：45.5*31*44.5 CM', 40, 11.9, 12.9, (45.5, 31, 44.5)),  # 双点手误
    ('QTY：60 PCS\nN.W.：15 KGS\nG.W.：16.8 KGS\nMEAS：44*39*39 CM', 60, 15, 16.8, (44, 39, 39)),
    ('QTY：40 PCS\nN.W.：24.5 KGS\nG.W.：25.7 KGS\nMEAS：68*43.5*50 CM', 40, 24.5, 25.7, (68, 43.5, 50)),
    ('QTY：24 PCS\nN.W.：13.6 KGS\nG.W.：14.5 KGS\nMEAS：42*38*49 CM\n（套装）', 24, 13.6, 14.5, (42, 38, 49)),
    ('QTY：40 PCS\nN.W.：21.0 KGS\nG.W.：22.2 KGS\nMEAS：66*44.5*44 CM', 40, 21.0, 22.2, (66, 44.5, 44)),
    ('QTY:   80 PCS\nN.W.:  14.4  KGS       \nG.W.:  15.5 KGS         \nMEAS:37.5*32*38CM', 80, 14.4, 15.5, (37.5, 32, 38)),
    ('QTY：20 PCS\nN.W.：10.9 KGS\nG.W.：11.6 KGS\nMEAS：42*31*43.5 CM', 20, 10.9, 11.6, (42, 31, 43.5)),
    ('QTY：60 PCS\nN.W.：14.3 KGS\nG.W.：15.2 KGS\nMEAS：47.5*40.5*40.5CM', 60, 14.3, 15.2, (47.5, 40.5, 40.5)),
    ('QTY：40 PCS\nN.W.：12.7 KGS\nG.W.：13.5 KGS\nMEAS：48.5*32.5*33cm', 40, 12.7, 13.5, (48.5, 32.5, 33)),
    ('QTY：40 PCS\nN.W.：18.1 KGS\nG.W.：19.1 KGS\nMEAS：60*33.5*54 CM', 40, 18.1, 19.1, (60, 33.5, 54)),
    ('QTY：60 PCS\nN.W.：15.3 KGS\nG.W.：16.3 KGS\nMEAS：42*33*42 CM', 60, 15.3, 16.3, (42, 33, 42)),
    ('QTY：12 PCS\nN.W.：15.4 KGS\nG.W.：16.5 KGS\nMEAS：46*44*35 CM', 12, 15.4, 16.5, (46, 44, 35)),
]


@pytest.mark.parametrize('text,pcs,nw,gw,dims', REAL_CTN)
def test_parse_all_real_ctn_specs(text, pcs, nw, gw, dims):
    out = quote.parse_ctn_spec(text)
    assert out['pcs'] == pcs
    assert out['nw'] == nw
    assert out['gw'] == gw
    assert out['dims'] == dims
    assert out['meas'] == '*'.join(str(d) for d in dims)


def test_parse_garbage_returns_empty():
    assert quote.parse_ctn_spec('') == {}
    assert quote.parse_ctn_spec('随便写的') == {}
    assert quote.parse_ctn_spec(None) == {}


@pytest.mark.parametrize('text,dims', [
    ('60*40*40', (60, 40, 40)),
    ('50.5*35.5*42.5', (50.5, 35.5, 42.5)),
    ('52*42*46 CM', (52, 42, 46)),
    ('39.5X28X44.5CM', (39.5, 28, 44.5)),
    ('40', None),
    ('', None),
])
def test_parse_bare_dims_from_ctn_text(text, dims):
    """纯尺寸文本（无 QTY/N.W./G.W. 前缀）也能解出 MEAS——动态分类只有箱规文本一列，
    裸尺寸是常见形态。"""
    out = quote.parse_ctn_spec(text)
    assert out.get('dims') == dims


def test_ceil_edges():
    assert quote._ceil_div(80, 40) == 2      # 整除
    assert quote._ceil_div(81, 40) == 3      # 差1进位
    assert quote._ceil_div(1, 40) == 1
    assert quote._ceil_div(100, 40) == 3


# ---------- 生成：逐属性断言（纯代码版布局：行1列头，行2起数据） ----------

FIELDS = [
    {'key': 'model', 'label': '型号', 'type': 'text', 'visibility': 'public',
     'searchable': True, 'role': 'model', 'required': False},
    {'key': 'spec', 'label': '描述', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
    {'key': 'color', 'label': '颜色', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
    {'key': 'price', 'label': '出厂价', 'type': 'money', 'visibility': 'internal',
     'searchable': False, 'role': 'price', 'required': False},
    {'key': 'ctn', 'label': '箱规', 'type': 'text', 'visibility': 'public',
     'searchable': False, 'role': 'spec', 'required': False},
]


def _mkdb(tmp_path, n=3, dims_only=True):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    rows = [{'id': f'r{i}', 'inner_code': f'KS-R{i}',
             'data': {'model': f'M{i}', 'spec': f'描述{i}', 'color': '黑色',
                      'ctn': REAL_CTN[i][0], 'price': str(10 + i * 5)},
             'images': [st.save('test_cat', f'r{i}', 'm.png', PNG)]}
            for i in range(n)]
    seed_products(conn, rows, key='test_cat', name='测试品类', fields=FIELDS)
    if dims_only:
        # 第二个分类：只有 型号/出厂价/箱规（无描述颜色、箱规文本无重量）
        seed_products(conn, [{
            'id': 'c0', 'inner_code': 'KS-C0',
            'data': {'model': '8226', 'price': '21.5',
                     'ctn': 'QTY：40 PCS\nMEAS：60*40*40 CM'},
            'images': [st.save('test_dims', 'c0', 'm.png', PNG)]}],
            key='test_dims', name='裸箱规品类',
            fields=[FIELDS[0], FIELDS[3], FIELDS[4]])
    return conn, st


def test_header_row_and_layout(tmp_path):
    """行1=14列头（加粗+底色+列宽沿用），行2起数据，行高 27/100。"""
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    out = str(tmp_path / 'layout.xlsx')
    quote.generate_generic(conn, st, [{'category': 'test_cat', 'product_id': 'r0',
                                       'quantity': 100}], 0, out)
    ws = openpyxl.load_workbook(out).active
    assert ws.title == '报价单'
    for c, label in enumerate(HEADERS, 1):
        cell = ws.cell(1, c)
        assert cell.value == label
        assert cell.font.bold
        assert cell.fill.fill_type == 'solid'
    widths = {col: round(ws.column_dimensions[col].width, 1)
              for col in 'ABCDEFGHIJKLMN'}
    assert widths == {'A': 18.8, 'B': 45.0, 'C': 35.0, 'D': 22.6, 'E': 11.4,
                      'F': 11.7, 'G': 18.1, 'H': 11.7, 'I': 19.7, 'J': 13.2,
                      'K': 11.1, 'L': 12.6, 'M': 12.6, 'N': 12.6}
    assert ws.row_dimensions[1].height == 27
    assert ws.row_dimensions[2].height == 100
    assert ws.cell(2, 1).value == 'M0'                       # 数据从行2起
    assert ws.max_row == 6                                   # 1列头+1数据+3合计


def test_full_fields_three_items(tmp_path):
    """3款：14列逐格断言 + 嵌图数 + 合计/定金/尾款（全部是值不是公式）。"""
    conn, st = _mkdb(tmp_path, n=2)
    out = str(tmp_path / 'q3.xlsx')
    quote.generate_generic(conn, st, [
        {'category': 'test_cat', 'product_id': 'r0', 'quantity': 100},   # 40/箱 → 3箱
        {'category': 'test_cat', 'product_id': 'r1', 'quantity': 60},    # 60/箱 → 1箱
        {'category': 'test_dims', 'product_id': 'c0', 'quantity': 80},   # 40/箱 → 2箱（无重量）
    ], 3, out, deposit_pct=20)
    ws = openpyxl.load_workbook(out).active
    # 行2：r0（40/箱 毛重17.7 净重16.9 38.5*37.5*42.5，+3% 单价10.30）
    r = 2
    assert ws.cell(r, 1).value == 'M0'
    assert ws.cell(r, 3).value == '描述0'
    assert ws.cell(r, 4).value == '黑色'
    assert ws.cell(r, 5).value == 10.30                    # E 价格
    assert ws.cell(r, 6).value == 120                      # F 数量=整箱(40×3)
    assert ws.cell(r, 7).value == 1236                     # G 小计=值（10.30×120整箱）
    assert ws.cell(r, 8).value == 40                       # H 每箱
    assert ws.cell(r, 9).value == 3                        # I 箱数 ⌈100/40⌉
    assert ws.cell(r, 10).value == 17.7                    # J 毛重
    assert ws.cell(r, 11).value == 16.9                    # K 净重
    assert ws.cell(r, 12).value == '38.5*37.5*42.5'        # L MEAS
    assert ws.cell(r, 13).value == round(3 * 17.7, 2)      # M 总毛重 53.1
    assert ws.cell(r, 14).value == round(3 * (38.5 * 37.5 * 42.5) / 1e6, 3)  # N 总体积
    # 行3：r1（60/箱 → 1箱）
    r = 3
    assert ws.cell(r, 9).value == 1
    assert ws.cell(r, 13).value == round(1 * 19.0, 2)
    # 行4：裸箱规分类（缺重量留空、描述颜色留空）
    r = 4
    assert ws.cell(r, 3).value in (None, '')               # C 描述空
    assert ws.cell(r, 4).value in (None, '')               # D 颜色空
    assert ws.cell(r, 5).value == 22.15                    # decimal half up
    assert ws.cell(r, 8).value == 40                       # 装箱数
    assert ws.cell(r, 9).value == 2                        # ⌈80/40⌉
    assert ws.cell(r, 10).value is None and ws.cell(r, 11).value is None  # 无重量
    assert ws.cell(r, 12).value == '60*40*40'
    assert ws.cell(r, 13).value is None                    # M 空
    assert ws.cell(r, 14).value == round(2 * (60 * 40 * 40) / 1e6, 3)
    # 合计：3款 → TOTAL 行=2+3=5，之后 DEPOSIT/BALANCE
    assert ws.cell(5, 1).value == '合计'
    assert ws.cell(5, 7).value == 3935                   # 1236+927+1772
    assert ws.cell(5, 9).value == 6                      # 3+1+2箱
    assert ws.cell(5, 13).value == 72.1                  # 53.1+19.0
    assert ws.cell(5, 14).value == round(round(3 * (38.5 * 37.5 * 42.5) / 1e6, 3) + 0.091 + 0.192, 3)
    # 定金 20%：总额3935 → 787；尾款差额（写值不写公式）
    assert ws.cell(6, 1).value == '定金'
    assert ws.cell(6, 6).value == 0.2
    assert ws.cell(6, 6).number_format == '0.##%'
    assert ws.cell(6, 7).value == round(3935 * 0.2, 2)
    assert ws.cell(7, 1).value == '尾款'
    assert ws.cell(7, 7).value == 3935 - round(3935 * 0.2, 2)
    for r_ in (5, 6, 7):
        assert isinstance(ws.cell(r_, 7).value, (int, float))   # 值不是 '=SUM(...)'
    assert ws.max_row == 8
    assert '需求 100' in ws.cell(8,1).value and '报价 120' in ws.cell(8,1).value
    # 图片：纯代码版无模板装饰图，只有 3 张商品主图，全部锚在 B 列对应数据行
    imgs = ws._images
    assert len(imgs) == 3
    assert sorted(im.anchor._from.row for im in imgs) == [1, 2, 3]   # 0基：数据行2-4
    assert all(im.anchor._from.col == 1 for im in imgs)              # B列


def test_multi_item_quote_with_adjustment(tmp_path):
    """多商品报价：各自数量 + 百分比调整 + 小计 + 合计；无图商品行不嵌图。"""
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
    out = str(tmp_path / 'multi.xlsx')
    quote.generate_generic(conn, st, [
        {'category': 'test_cat', 'product_id': 'p1', 'quantity': 100},
        {'category': 'test_cat', 'product_id': 'p2', 'quantity': 500},
    ], price_adjustment_pct=3, out_path=out)
    ws = openpyxl.load_workbook(out).active
    assert ws.cell(1, 6).value == '数量'
    assert ws.cell(1, 7).value == '金额'
    assert ws.cell(2, 1).value == '8226'          # ITEM NO.
    assert ws.cell(2, 5).value == 22.15           # 21.5*1.03=22.145 → 22.15
    assert ws.cell(2, 6).value == 120             # QUANTITY=整箱(40×3)
    assert ws.cell(2, 7).value == 22.15 * 120        # G 小计=值（整箱120）
    assert ws.cell(3, 1).value == '8227'
    assert ws.cell(3, 5).value == 30.90           # 30*1.03=30.90
    assert ws.cell(3, 6).value == 500
    assert ws.cell(3, 7).value == 30.90 * 500
    # 合计：k=2 → TOTAL 在 2+2=4
    assert ws.cell(4, 1).value == '合计'
    total = 22.15 * 120 + 30.90 * 500
    assert ws.cell(4, 7).value == total
    assert ws.cell(5, 7).value == round(total * 0.3, 2)  # 默认定金30%
    assert ws.cell(6, 7).value == total - round(total * 0.3, 2)
    assert len(ws._images) == 1                   # 只有 p1 有主图


def test_twenty_items_rows_and_totals(tmp_path):
    """20款：行数=款数（纯代码生成不再有插行/删空行路径），合计与嵌图数对。"""
    conn, st = _mkdb(tmp_path, n=20, dims_only=False)
    items = [{'category': 'test_cat', 'product_id': f'r{i}', 'quantity': 10 + i}
             for i in range(20)]
    out = str(tmp_path / 'q20.xlsx')
    quote.generate_generic(conn, st, items, 0, out)
    ws = openpyxl.load_workbook(out).active
    assert ws.cell(2, 1).value == 'M0' and ws.cell(21, 1).value == 'M19'
    T = 22                                                   # 2+20
    assert ws.cell(T, 1).value == '合计'
    total20 = 0
    for i in range(20):
        pcs = REAL_CTN[i][1]
        total20 += (10 + i * 5) * (pcs * math.ceil((10 + i) / pcs))  # 整箱数量
    assert ws.cell(T, 7).value == total20
    assert ws.cell(T + 2, 7).value == total20 - round(total20 * 0.3, 2)  # BALANCE
    assert len(ws._images) == 20                             # 20张商品图
    assert ws.max_row >= 24                                  # 1列头+20数据+3合计


def test_deposit_default_and_edges(tmp_path):
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    out = str(tmp_path / 'qd.xlsx')
    quote.generate_generic(conn, st, [{'category': 'test_cat', 'product_id': 'r0',
                                       'quantity': 100}], 0, out)
    ws = openpyxl.load_workbook(out).active
    T = 3                                                    # 2+1
    assert ws.cell(T + 1, 6).value == 0.3
    assert ws.cell(T + 1, 7).value == round(10 * 120 * 0.3, 2)      # 默认30%（整箱120台）
    # 定金100%（尾款0）与0%（定金0）不炸
    for pct in (0, 100):
        quote.generate_generic(conn, st, [{'category': 'test_cat', 'product_id': 'r0',
                                           'quantity': 7}],
                               0, str(tmp_path / f'q{pct}.xlsx'), deposit_pct=pct)
    ws2 = openpyxl.load_workbook(str(tmp_path / 'q100.xlsx')).active
    T2 = 3
    assert ws2.cell(T2 + 1, 7).value == round(10 * 40 * 1.0, 2)       # 100%定金=全款（7台→整箱40）
    assert ws2.cell(T2 + 2, 7).value == 0                             # 尾款0


def test_discount_negative_adjustment(tmp_path):
    """折扣场景：出厂价下浮 5%（单价保留两位小数）。"""
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    out = str(tmp_path / 'qdisc.xlsx')
    quote.generate_generic(conn, st, [{'category': 'test_cat', 'product_id': 'r0',
                                       'quantity': 100}], -5, out)
    ws = openpyxl.load_workbook(out).active
    assert ws.cell(2, 5).value == 9.50
    assert ws.cell(2, 7).value == 9.50 * 120   # 整箱


def test_discount_plus_custom_deposit_combo(tmp_path):
    """组合场景：下浮5% + 定金20%（用户最常改的三件套之二）。"""
    conn, st = _mkdb(tmp_path, n=2, dims_only=False)
    out = str(tmp_path / 'qcombo.xlsx')
    quote.generate_generic(conn, st, [
        {'category': 'test_cat', 'product_id': 'r0', 'quantity': 100},
        {'category': 'test_cat', 'product_id': 'r1', 'quantity': 60},
    ], -5, out, deposit_pct=20)
    ws = openpyxl.load_workbook(out).active
    u0, u1 = 9.50, 14.25
    assert ws.cell(2, 5).value == u0
    assert ws.cell(3, 5).value == u1
    T = 4                                                    # 2+2
    total = 9.50 * 120 + 14.25 * 60   # 100→整箱120；60恰一箱
    assert ws.cell(T, 7).value == total
    assert ws.cell(T + 1, 7).value == round(total * 0.2, 2)   # 定金20%
    assert ws.cell(T + 2, 7).value == total - round(total * 0.2, 2)
    assert round(total * 0.2, 2) + (total - round(total * 0.2, 2)) == total


def test_price_zero_and_tiny(tmp_path):
    """边界：出厂价 0 / 大幅上浮 50%。"""
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    conn.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.price','0') WHERE id='r0'")
    conn.commit()
    out = str(tmp_path / 'q0.xlsx')
    quote.generate_generic(conn, st, [{'category': 'test_cat', 'product_id': 'r0',
                                       'quantity': 50}], 50, out)
    ws = openpyxl.load_workbook(out).active
    assert ws.cell(2, 5).value == 0


def test_quote_map_missing_rejected(tmp_path):
    """quote_map 未配置（价格列缺映射）→ 明确报错拒单，不猜口径。"""
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st = LocalStorage(str(tmp_path))
    no_price_fields = [
        {'key': 'model', 'label': '型号', 'type': 'text', 'visibility': 'public',
         'searchable': True, 'role': 'model', 'required': False},
        {'key': 'spec', 'label': '规格', 'type': 'text', 'visibility': 'public',
         'searchable': False, 'role': 'spec', 'required': False},
    ]
    seed_products(conn, [{'id': 'n0', 'data': {'model': 'X1', 'spec': '无价'}}],
                  key='no_price_cat', name='无价格列品类', fields=no_price_fields)
    with pytest.raises(ValueError, match='未配置报价字段映射'):
        quote.generate_generic(conn, st, [{'category': 'no_price_cat',
                                           'product_id': 'n0', 'quantity': 10}],
                               0, str(tmp_path / 'never.xlsx'))
    assert not os.path.exists(str(tmp_path / 'never.xlsx'))   # 拒单不出半截文件


def test_sample_xlsx_structure_in_tmp():
    """工单验收：生成一份样例到 /tmp，openpyxl 复核结构（供人工查看）。"""
    tmp_dir = '/tmp'
    db_path = os.path.join(str(tmp_dir), 'quote-generic-sample.sqlite')
    out = os.path.join(str(tmp_dir), 'quote-generic-sample.xlsx')
    for stale in (db_path, out):
        if os.path.exists(stale):
            os.remove(stale)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    st_dir = os.path.join(str(tmp_dir), 'quote-generic-sample-store')
    st = LocalStorage(st_dir)
    seed_products(conn, [
        {'id': f's{i}', 'data': {'model': f'SAMPLE-{i}', 'spec': f'样例描述{i}', 'color': '白色',
                                 'ctn': REAL_CTN[i][0], 'price': str(20 + i)},
         'images': [st.save('sample_cat', f's{i}', 'm.png', PNG)]}
        for i in range(2)], key='sample_cat', name='样例品类')
    conn.commit()
    try:
        quote.generate_generic(conn, st, [
            {'category': 'sample_cat', 'product_id': 's0', 'quantity': 100},
            {'category': 'sample_cat', 'product_id': 's1', 'quantity': 130},
        ], 3, out, deposit_pct=30)
        ws = openpyxl.load_workbook(out).active
        assert [ws.cell(1, c).value for c in range(1, 15)] == HEADERS
        assert ws.cell(2, 1).value == 'SAMPLE-0' and ws.cell(3, 1).value == 'SAMPLE-1'
        assert ws.cell(4, 1).value == '合计' and ws.cell(5, 1).value == '定金' \
            and ws.cell(6, 1).value == '尾款'
        assert len(ws._images) == 2
    finally:
        conn.close()
        os.remove(db_path)     # 只留 xlsx 样例
        shutil.rmtree(st_dir, ignore_errors=True)

@pytest.mark.parametrize('deposit', [0, 100])
def test_decimal_quote_carton_and_deposit_boundaries(tmp_path, deposit):
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    conn.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.price','65','$.ctn','40件/箱') WHERE id='r0'")
    out = str(tmp_path / 'decimal.xlsx')
    quote.generate_generic(conn, st, [{'category':'test_cat', 'product_id':'r0', 'quantity':50}], 3, out, deposit_pct=deposit)
    ws = openpyxl.load_workbook(out).active
    assert ws['E2'].value == 66.95
    assert ws['F2'].value == 80
    assert ws['G2'].value == 5356
    assert ws['G4'].value == (5356 if deposit else 0)
    assert ws['G5'].value == (0 if deposit else 5356)

@pytest.mark.parametrize('text', ['40', '40 PCS/CTN', '40件/箱', '装箱数：40', '40pcs'])
def test_common_carton_counts(text):
    assert quote.parse_ctn_spec(text)['pcs'] == 40


def test_separate_carton_weight_dimensions_mapping(tmp_path):
    from catalog import dynamic_catalog as dc
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    tpl = dc.get_template(conn, 'test_cat')
    for key, label in [('pcs','每箱数量'),('gross','毛重'),('net','净重'),('dimensions','包装尺寸')]:
        tpl['fields'].append({'key':key,'label':label,'type':'text','role':'spec'})
    dc.approve_template(conn,tpl,expected_version=1)
    dc.set_quote_map(conn,'test_cat',{'model_field':'model','price_field':'price','pcs_field':'pcs','gw_field':'gross','nw_field':'net','dims_field':'dimensions'})
    conn.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.pcs','40','$.gross','12.5kg','$.net','11kg','$.dimensions','60*40*30') WHERE id='r0'")
    out = str(tmp_path / 'separate.xlsx')
    details = []
    quote.generate_generic(conn,st,[{'category':'test_cat','product_id':'r0','quantity':50}],0,out,details=details)
    ws = openpyxl.load_workbook(out).active
    assert [ws.cell(2,c).value for c in (8,9,10,11,12,13,14)] == [40,2,12.5,11,'60*40*30',25,0.144]
    assert details[0]['quoted_quantity'] == 80


def test_negative_adjustment_floor_and_decimal_half_up(tmp_path):
    conn, st = _mkdb(tmp_path, n=1, dims_only=False)
    items = [{'category':'test_cat','product_id':'r0','quantity':1}]
    out = str(tmp_path / 'boundary.xlsx')
    quote.generate_generic(conn, st, items, -100, out)
    assert openpyxl.load_workbook(out).active['G2'].value == 0
    with pytest.raises(ValueError, match='价格调整'):
        quote.generate_generic(conn, st, items, -100.01, out)
    conn.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.price','1.005') WHERE id='r0'")
    quote.generate_generic(conn, st, items, 0, out)
    assert openpyxl.load_workbook(out).active['E2'].value == 1.01
