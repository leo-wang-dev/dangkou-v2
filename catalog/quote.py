"""报价单（纯代码生成）：14 列通用格式，不依赖任何模板文件。

第1行=列头 → 数据行（PHOTO 嵌商品主图）→ TOTAL/DEPOSIT/BALANCE 三行（写值不写公式，
手机/WPS 预览不重算公式也处处可见）。取数走 quote_map 字段映射，整箱/毛重/体积计算链、
parse_ctn_spec 箱规解析沿用 v2.2。
"""
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from . import cs_i18n


_HEADERS = ('ITEM NO.', 'PHOTO', 'DESCRIPTION', 'COLORS', 'PRICE', 'QUANTITY',
            'TOTAL AMOUNT', 'PCS/CTN', 'CTNS', 'G.W/CTN', 'N.W/CTN', 'MEAS',
            'T.G.W', 'T-CBM')
# 列宽沿用 v2.2 商家模板实测值（B=PHOTO 放图最宽，C=DESCRIPTION 次之）
_COL_WIDTHS = {'A': 18.8, 'B': 45, 'C': 35, 'D': 22.6, 'E': 11.4, 'F': 11.7,
               'G': 18.1, 'H': 11.7, 'I': 19.7, 'J': 13.2, 'K': 11.1, 'L': 12.6,
               'M': 12.6, 'N': 12.6}
_HEADER_ROW_H, _DATA_ROW_H = 27, 100


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
    if 'pcs' not in out:
        count = re.fullmatch(r'\s*(?:(?:装箱数|每箱|QTY)[：:]?\s*)?' + _NUM + r'\s*(?:(?:PCS|件|个|台)(?:\s*/\s*(?:CTN|箱))?)?\s*', s, re.I)
        if count and float(count[1]).is_integer() and float(count[1]) > 0:
            out['pcs'] = int(float(count[1]))
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
        ctn = parse_ctn_spec(ctn_text) if ctn_text else {}
        if qmap.get('pcs_field'):
            ctn.update({k: v for k, v in parse_ctn_spec(val(qmap['pcs_field'])).items() if k == 'pcs'})
        for name in ('gw', 'nw'):
            match = re.fullmatch(r'\s*' + _NUM + r'\s*(?:KGS?|千克|公斤)?\s*', val(qmap.get(name + '_field')), re.I)
            if match:
                ctn[name] = float(match[1])
        if qmap.get('dims_field'):
            ctn.update({k: v for k, v in parse_ctn_spec(val(qmap['dims_field'])).items() if k in {'dims', 'meas'}})
        return {'item': val(qmap['model_field']),
                'supplier': p['supplier'] or template.get('supplier', ''),
                'desc': ' / '.join(v for v in (val(f['key']) for f in desc_fields) if v),
                'color': val(qmap.get('color_field')),
                'price': val(qmap['price_field']),
                'ctn': ctn}
    return extract


def generate_generic(conn, storage, items, price_adjustment_pct, out_path, deposit_pct: float = 30, *, details: list | None = None, target_language="zh", llm=None):
    """纯代码生成报价单：行1=列头，行2起=数据（嵌图），末三行 TOTAL/DEPOSIT/BALANCE（写值）。"""
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

    wb = openpyxl.Workbook()
    ws = wb.active
    lang = cs_i18n.normalize_language(target_language)
    ws.title = cs_i18n.t('quotation',lang)[:31]
    ws.sheet_view.rightToLeft = lang == 'ar'
    for col, width in _COL_WIDTHS.items():
        ws.column_dimensions[col].width = width

    # 行1：列头（加粗+底色+居中，不追求花哨）
    ws.row_dimensions[1].height = _HEADER_ROW_H
    header_fill = PatternFill('solid', fgColor='D9E1F2')
    for c, label in enumerate([cs_i18n.t(k,lang) for k in ('modelHeader','photoHeader','description','colorHeader','priceHeader','quantityHeader','amount','cartonHeader','cartons','grossWeight','netWeight','measurements','totalWeight','volume')], 1):
        cell = ws.cell(1, c, label)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # 数字格式约定：物流列纯数字（0/0.0/0.000），钱只在 E 单价 / G 小计 / 合计 / 定金 / 尾款
    _FMT = {5: '0.00', 7: '0.00', 8: '0', 9: '0', 10: '0.0', 11: '0.0',
            12: 'General', 13: '0.0', 14: '0.000'}
    sums = {'amount': Decimal('0'), 'ctns': 0, 'gw': 0.0, 'cbm': 0.0}
    DATA_START = 2

    for i, (extract, p, qty) in enumerate(prows):
        r = DATA_START + i
        ws.row_dimensions[r].height = _DATA_ROW_H
        row = extract(p)
        try:
            # 商家表价格常带单位/附注（实发：「28.5元/台\n（不含税运）」「AC：42.5元/台 DC：32元/台」）。
            # 逐字入库不动原文，出报价单时取文本中的首个金额数字（多价取第一个，如 AC/DC 双规格）；
            # 提不出数字才按无效报错——数字来自商家原文，不是编造。
            price_text = str(row['price'] or '0').replace('¥', '').replace(',', '').replace('￥', '')
            try:
                base = Decimal(price_text)
            except InvalidOperation:
                match = re.search(r'\d+(?:\.\d+)?', price_text)
                if not match:
                    raise
                base = Decimal(match.group())
            if not base.is_finite() or base < 0:
                raise ValueError('商品价格必须为有效非负金额')
            unit = (base * (1 + Decimal(str(price_adjustment_pct)) / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError('商品价格格式无效') from exc
        requested_qty = qty
        ctn = row['ctn']
        pcs, gw, nw = ctn.get('pcs'), ctn.get('gw'), ctn.get('nw')
        meas, dims = ctn.get('meas'), ctn.get('dims')
        ctns = _ceil_div(qty, pcs) if pcs else None
        if ctns is not None:
            qty = pcs * ctns          # 整箱口径：QUANTITY = 每箱数×箱数（100台/60箱装→2箱=120台）
        if details is not None:
            details.append({'category': p['category_key'], 'product_id': p['id'],
                            'supplier': row['supplier'], 'model': row['item'],
                            'requested_quantity': requested_qty, 'quoted_quantity': qty,
                            'pcs_per_carton': pcs, 'unit_price': float(unit), 'amount': float(unit * qty)})
        tgw = round(ctns * gw, 2) if (ctns is not None and gw) else None
        tcbm = round(ctns * (dims[0] * dims[1] * dims[2]) / 1e6, 3) \
            if (ctns is not None and dims) else None

        ws.cell(r, 1, str(row['item'] or '')).alignment = Alignment(readingOrder=1)                    # A ITEM NO.
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
        for c_, fmt_ in _FMT.items():
            ws.cell(r, c_).number_format = fmt_
        sums['amount'] += amount
        sums['ctns'] += ctns or 0
        sums['gw'] += tgw or 0
        sums['cbm'] += tcbm or 0

    # 合计/定金/尾款：数据区后三行，写计算值（预览器不重算公式，值才处处可见）
    T = DATA_START + len(prows)
    for r, label in ((T, 'TOTAL'), (T + 1, 'DEPOSIT'), (T + 2, 'BALANCE')):
        cell = ws.cell(r, 1, cs_i18n.t(label.lower(),lang))
        cell.font = Font(bold=True)
    deposit = (sums['amount'] * Decimal(str(deposit_pct)) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    ws.cell(T, 7, sums['amount']).number_format = '0.00'
    ws.cell(T, 9, sums['ctns']).number_format = '0'
    ws.cell(T, 13, round(sums['gw'], 2)).number_format = '0.0'
    ws.cell(T, 14, round(sums['cbm'], 3)).number_format = '0.000'
    ws.cell(T + 1, 6, Decimal(str(deposit_pct)) / 100).number_format = '0.##%'
    ws.cell(T + 1, 7, deposit).number_format = '0.00'            # DEPOSIT = 总额×定金%
    ws.cell(T + 2, 7, round(sums['amount'] - deposit, 2)).number_format = '0.00'  # BALANCE = 差额

    # Customer narrative terms have a separate translation boundary; raw models,
    # suppliers, quantities and prices never enter the translation batch.
    raw = [ws.cell(r,c).value or '' for r in range(2,T) for c in (3,4)]
    protected = [v for extract,p,_ in prows for v in (extract(p)['supplier'],extract(p)['item']) if v]
    translated = cs_i18n.translate_texts(conn,llm,lang,raw,protected=protected,
        commit=False,max_missing=0 if conn.in_transaction or llm is None else None)
    for (r,c),value in zip(((r,c) for r in range(2,T) for c in (3,4)),translated):
        ws.cell(r,c,value)
    adjustments=[]
    for extract,p,requested in prows:
        row=extract(p);pcs=row['ctn'].get('pcs')
        quoted=pcs*_ceil_div(requested,pcs) if pcs else requested
        if quoted!=requested:
            adjustments.append(cs_i18n.t('cartonAdjustment',lang,model=row['item'],requested=requested,quoted=quoted,pcs=pcs))
    for value in adjustments:
        ws.append([value])
    for row in ws:
        for cell in row:
            if cell.data_type == 'f': cell.data_type = 's'
    wb.save(out_path)
    return out_path
