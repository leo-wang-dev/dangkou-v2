"""报价单 v2.2：ELETRO BELEZA 全字段模板（14列含装箱物流+合计+定金），输出可直接转发客户。"""
import json
import os
import math
import re

import openpyxl

TEMPLATE_V2_PATH = os.environ.get('CATALOG_QUOTE_TEMPLATE') or os.path.join(os.path.dirname(__file__), '..', 'data', 'quote_template_v2.xlsx')


# ================= 报价单 v2.2：ELETRO BELEZA 模板（全字段 + 动态行数 + 动态定金） =================

_NUM = r'(\d+(?:\.\d+)?)'


def _clean(text) -> str:
    """修商家手误：'12..9'→'12.9'（生产库实测见过）。"""
    return re.sub(r'\.{2,}', '.', str(text or ''))


def parse_ctn_spec(text) -> dict:
    """箱规文本四件套 → {pcs, nw, gw, meas, dims}，解析不到的键缺省。

    生产库实测变体（全过测试）：全角/半角冒号、'QTY: 40PCS' 无空格、
    'MEAS:39.5X28X44.5CM' X分隔无空格、'G.W.：12..9' 双点、'（单机）'尾巴注释。
    """
    s = _clean(text)
    out = {}
    m = re.search(r'QTY[：:]?\s*' + _NUM + r'\s*PCS', s, re.I)
    if m:
        out['pcs'] = int(float(m.group(1)))
    m = re.search(r'N\.?\s*W\.?[：:]?\s*' + _NUM + r'\s*KGS?', s, re.I)
    if m:
        out['nw'] = float(m.group(1))
    m = re.search(r'G\.?\s*W\.?[：:]?\s*' + _NUM + r'\s*KGS?', s, re.I)
    if m:
        out['gw'] = float(m.group(1))
    m = re.search(r'MEAS[：:]?\s*' + _NUM + r'\s*[*xX×]\s*' + _NUM + r'\s*[*xX×]\s*' + _NUM,
                  s, re.I)
    if m is None:
        # 动态分类的箱规列常只有裸尺寸（旧版结构化装箱尺寸字段删除后统一走箱规文本）：
        # 无 MEAS 前缀的 三段数字×3 也按尺寸收——认不出就留空，不猜。
        m = re.fullmatch(r'\s*' + _NUM + r'\s*[*xX×]\s*' + _NUM + r'\s*[*xX×]\s*' + _NUM
                         + r'\s*(?:cm|CM)?\s*', s)
    if m:
        dims = tuple(float(m.group(i)) for i in (1, 2, 3))
        out['meas'] = f'{_num(dims[0])}*{_num(dims[1])}*{_num(dims[2])}'
        out['dims'] = dims
    return out


def _num(x):
    """17.0→17、17.7→17.7：写进单元格的数字尽量干净。"""
    f = float(x)
    return int(f) if f.is_integer() else round(f, 4)


def _shift_below(ws, at_row: int, delta: int):
    """在 at_row 处插行(delta>0)/删行(delta<0)，openpyxl 只搬单元格——
    行高、合并单元格、图片锚点必须自己搬（收款码图/备注行/合计区全靠这个）。"""
    n = abs(delta)
    heights = {r: ws.row_dimensions[r].height for r in list(ws.row_dimensions)
               if r >= at_row and ws.row_dimensions[r].height is not None}
    merges = [(m.min_row, m.min_col, m.max_row, m.max_col)
              for m in list(ws.merged_cells.ranges) if m.min_row >= at_row]
    for m in list(ws.merged_cells.ranges):
        if m.min_row >= at_row:
            ws.merged_cells.remove(m)
    anchors = [im.anchor for im in ws._images
               if im.anchor._from and im.anchor._from.row >= at_row - 1]
    if delta > 0:
        ws.insert_rows(at_row, n)
    else:
        ws.delete_rows(at_row, n)
    for r in list(heights):
        ws.row_dimensions[r].height = None
    for r, h in heights.items():
        ws.row_dimensions[r + delta].height = h
    for r1, c1, r2, c2 in merges:
        ws.merge_cells(start_row=r1 + delta, start_column=c1,
                       end_row=r2 + delta, end_column=c2)
    for a in anchors:
        a._from.row += delta
        if getattr(a, '_to', None) is not None:
            a._to.row += delta


def _ceil_div(qty: int, pcs: int) -> int:
    return (qty + pcs - 1) // pcs


# B列(宽45字符)≈320px，数据行高100pt≈133px——图片统一框 + 单元格内居中
_PHOTO_CELL_W, _PHOTO_CELL_H = 320, 133
_PHOTO_BOX_W, _PHOTO_BOX_H = 130, 118


def _add_photo(ws, r: int, path: str):
    """商品图放进 B{r} 单元格：等比缩放进统一框(130x118)，水平垂直居中（EMU偏移锚点）。"""
    from PIL import Image as PILImage
    from openpyxl.drawing.image import Image as XLImg
    from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU

    with PILImage.open(path) as im:
        w0, h0 = im.size
    if not w0 or not h0:
        raise ValueError('空图')
    scale = min(_PHOTO_BOX_W / w0, _PHOTO_BOX_H / h0)   # 统一框：小图也放大到框，视觉一致
    iw, ih = int(w0 * scale), int(h0 * scale)
    xi = XLImg(path)
    marker = AnchorMarker(col=1, colOff=pixels_to_EMU((_PHOTO_CELL_W - iw) // 2),
                          row=r - 1, rowOff=pixels_to_EMU((_PHOTO_CELL_H - ih) // 2))
    xi.anchor = OneCellAnchor(_from=marker,
                              ext=XDRPositiveSize2D(pixels_to_EMU(iw), pixels_to_EMU(ih)))
    ws.add_image(xi)


def _dynamic_extractor(conn, category_key):
    """动态分类取数：按 quote_map 映射。型号/价格缺映射时明确拒单，不猜口径。"""
    from . import dynamic_catalog
    try:
        template = dynamic_catalog.get_template(conn, category_key)
    except KeyError:
        raise ValueError('商品品类无效')
    if template['storage'] != 'dynamic':
        raise ValueError('商品品类无效')
    qmap = template.get('quote_map') or {}
    missing = [name for name in ('model_field', 'price_field') if not qmap.get(name)]
    if missing:
        raise ValueError(f'分类「{template["name"]}」未配置报价字段映射（缺{"、".join(missing)}），'
                         '请先指定报价用哪一列（型号列和价格列）后再出正式报价单')
    skip = {qmap.get('model_field'), qmap.get('price_field'),
            qmap.get('ctn_field'), qmap.get('color_field')}
    desc_fields = [field for field in template['fields']
                   if field['key'] not in skip and field.get('role') == 'spec'][:4]

    def extract(p):
        try:
            data = json.loads(p['data_json'] or '{}')
        except (TypeError, ValueError):
            data = {}

        def val(key):
            value = data.get(key) if key else None
            return str(value) if value not in (None, '') else ''
        ctn_text = val(qmap.get('ctn_field'))
        return {'item': val(qmap['model_field']),
                'desc': ' / '.join(v for v in (val(f['key']) for f in desc_fields) if v),
                'color': val(qmap.get('color_field')),
                'price': val(qmap['price_field']),
                'ctn': parse_ctn_spec(ctn_text) if ctn_text else {}}
    return extract


def generate_v2(conn, storage, items, price_adjustment_pct, out_path, deposit_pct: float = 30):
    """ELETRO BELEZA 模板填充：14列全字段，行数=商品数（插行/删空行），
    合计/DEPOSIT/BALANCE 公式按实际行数重写，定金比例动态。"""
    from copy import copy
    from openpyxl.drawing.image import Image as XLImg
    from openpyxl.styles import Alignment

    if not math.isfinite(price_adjustment_pct) or price_adjustment_pct < -100:
        raise ValueError('价格调整百分比无效')
    if not math.isfinite(deposit_pct) or not 0 <= deposit_pct <= 100:
        raise ValueError('定金必须在 0 到 100% 之间')
    # 先取数（无效商品剔除后再定行数）
    prows = []
    for item in items:
        category = item.get('category', '')
        extract = _dynamic_extractor(conn, category)
        p = conn.execute('SELECT * FROM product_dynamic WHERE id=? AND category_key=?',
                         (item['product_id'], category)).fetchone()
        if p is None or p['status'] != 'approved':
            raise ValueError('商品不存在或已下架')
        qty = item.get('quantity', 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError('商品数量必须为正整数')
        prows.append((extract, p, qty))
    if not prows:
        raise ValueError('没有可报价的商品')
    k = len(prows)

    wb = openpyxl.load_workbook(TEMPLATE_V2_PATH)   # 商家真模板（ELETRO BELEZA 单Sheet）
    ws = wb.active
    DATA_START, TMPL_ROWS = 18, 6                    # 模板自带 18-23 六个数据行
    if k > TMPL_ROWS:                                # 扩容：插行 + 照18行补样式
        _shift_below(ws, DATA_START + TMPL_ROWS, k - TMPL_ROWS)
        for r in range(DATA_START + TMPL_ROWS, DATA_START + k):
            ws.row_dimensions[r].height = ws.row_dimensions[DATA_START].height
            for c in range(1, 15):
                ws.cell(r, c)._style = copy(ws.cell(DATA_START, c)._style)
    elif k < TMPL_ROWS:                              # 收紧：删空行（合计/收款/条款整体上移）
        _shift_below(ws, DATA_START + k, -(TMPL_ROWS - k))
    last = DATA_START + k - 1

    # 数字格式约定：物流列绝不能带货币符号（模板残留 ￥ 格式，填数前必须重设）；
    # 钱只出现在 E 单价 / G 小计 / 合计 / 定金 / 尾款。
    _FMT = {8: '0', 9: '0', 10: '0.0', 11: '0.0', 12: 'General', 13: '0.0', 14: '0.000'}
    sums = {'amount': 0, 'ctns': 0, 'gw': 0.0, 'cbm': 0.0}

    for i, (extract, p, qty) in enumerate(prows):
        r = DATA_START + i
        row = extract(p)
        base = float(str(row['price'] or '0').replace('¥', '').replace(',', '')) or 0
        unit = round(base * (1 + price_adjustment_pct / 100))
        ctn = row['ctn']
        pcs, gw, nw = ctn.get('pcs'), ctn.get('gw'), ctn.get('nw')
        meas, dims = ctn.get('meas'), ctn.get('dims')
        ctns = _ceil_div(qty, pcs) if pcs else None
        if ctns is not None:
            qty = pcs * ctns          # 整箱口径：QUANTITY = 每箱数×箱数（100台/60箱装→2箱=120台）
        tgw = round(ctns * gw, 2) if (ctns is not None and gw) else None
        tcbm = round(ctns * (dims[0] * dims[1] * dims[2]) / 1e6, 3) \
            if (ctns is not None and dims) else None

        ws.cell(r, 1, str(row['item'] or ''))                    # A ITEM NO.
        if p['image_main']:                                    # B PHOTO（统一框+居中）
            try:
                _add_photo(ws, r, storage.abs_path(p['image_main']))
            except Exception as e:  # noqa: BLE001
                print(f'[quote] 图嵌失败 {p["id"]}: {e}', flush=True)
        c = ws.cell(r, 3, row['desc'])                           # C DESCRIPTION
        c.alignment = Alignment(wrap_text=True, vertical='top')
        ws.cell(r, 4, row['color'])                              # D COLORS
        ws.cell(r, 5, unit)                                    # E PRICE
        ws.cell(r, 6, qty)                                     # F QUANTITY
        amount = unit * qty
        ws.cell(r, 7, amount)                                  # G TOTAL AMOUNT（写值：手机/WPS预览不重算公式会空白）
        ws.cell(r, 8, _num(pcs) if pcs else None)              # H PCS/CTN
        ws.cell(r, 9, ctns)                                    # I CTNS = ⌈量/每箱⌉
        ws.cell(r, 10, _num(gw) if gw else None)               # J G.W/CTN
        ws.cell(r, 11, _num(nw) if nw else None)               # K N.W/CTN
        ws.cell(r, 12, meas)                                   # L MEAS
        ws.cell(r, 13, tgw)                                    # M T.G.W = 箱数×单箱毛重
        ws.cell(r, 14, tcbm)                                   # N T-CBM = 箱数×单箱体积
        for c_, fmt_ in _FMT.items():                          # 物流列格式重设（模板残留￥必须压掉）
            ws.cell(r, c_).number_format = fmt_
        sums['amount'] += amount
        sums['ctns'] += ctns or 0
        sums['gw'] += tgw or 0
        sums['cbm'] += tcbm or 0

    # 合计/定金：按内容定位（插删行后位置会动），写计算值（预览器不重算公式，值才处处可见）
    T = next((rr for rr in range(last + 1, last + 12)
              if str(ws.cell(rr, 1).value or '').strip().upper() == 'TOTAL'), None)
    if T is None:
        raise ValueError('模板里找不到 TOTAL 行')
    ws[f'G{T}'] = sums['amount']
    ws[f'I{T}'] = sums['ctns']
    ws[f'M{T}'] = round(sums['gw'], 2)
    ws[f'M{T}'].number_format = '0.0'
    ws[f'N{T}'] = round(sums['cbm'], 3)
    ws[f'N{T}'].number_format = '0.000'
    deposit = round(sums['amount'] * deposit_pct / 100, 2)
    ws[f'G{T + 1}'] = deposit                                   # DEPOSIT
    ws[f'G{T + 2}'] = round(sums['amount'] - deposit, 2)        # BALANCE

    wb.save(out_path)
    return out_path
