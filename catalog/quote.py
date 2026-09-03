"""报价单：科森 5 列模板（型号/图片/价格/产品规格/起订量），输出可直接转发客户。"""
import os
import re
import threading
import uuid

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .templates import TEMPLATES

HEADERS = ['型号', '图片', '价格', '产品规格', '起订量']
COL_W = [16, 14, 10, 40, 10]


def _spec(t, p) -> str:
    if t.key == 'razor':
        parts = [p['size_mm'], p['color'], p['description']]
    else:
        parts = [p['voltage'], p['power'], p['material'], p['ctn_size']]
    return ' / '.join(str(x) for x in parts if x)


def _moq(t, p) -> str:
    if t.key == 'curler':
        return str(p['ctn_qty'] or '')
    m = re.search(r'(\d+)\s*(?:pcs|个|只|支)?', str(p['ctn_spec'] or ''), re.I)
    return m.group(1) if m else ''


def generate(conn, storage, category, product_ids, out_path) -> str:
    t = TEMPLATES[category]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '报价单'
    ws.append(HEADERS)
    for i, w in enumerate(COL_W, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].font = Font(bold=True)
    r = 2
    for pid in product_ids:
        p = conn.execute(f'SELECT * FROM {t.table} WHERE id=?', (pid,)).fetchone()
        if p is None:
            continue
        ws.cell(r, 1, p[t.dedup_field])
        ws.cell(r, 3, p['price'])
        ws.cell(r, 4, _spec(t, p))
        ws.cell(r, 5, _moq(t, p))
        ws.row_dimensions[r].height = 42          # ≈56px > 图52px：图不越行（5行显示成4的修复）
        if p['image_main']:
            try:
                xi = XLImage(storage.abs_path(p['image_main']))
                xi.width, xi.height = 52, 52
                ws.add_image(xi, f'B{r}')
            except Exception as e:  # noqa: BLE001
                print(f'[quote] 图嵌失败 {pid}: {e}', flush=True)
        r += 1
    wb.save(out_path)
    return out_path


# ---- 异步 job（对齐导入体验：立即返回+估时，完成即推文件）----
_JOBS = {}


def start_job(conn, storage, category, product_ids, out_dir) -> dict:
    import os
    os.makedirs(out_dir, exist_ok=True)
    job_id = uuid.uuid4().hex[:10]
    est = min(300, max(15, len(product_ids) * 3))
    _JOBS[job_id] = {'status': 'building', 'est_sec': est, 'path': None, 'error': None}

    def _bg():
        try:
            out = os.path.join(out_dir, f'quote-{job_id}.xlsx')
            generate(conn, storage, category, product_ids, out)
            _JOBS[job_id].update(status='done', path=out)
            from . import notify
            notify.push_file(f'📄 报价单已生成并发送（{len(product_ids)} 款）', out)
        except Exception as e:  # noqa: BLE001
            _JOBS[job_id].update(status='failed', error=str(e)[:200])

    threading.Thread(target=_bg, daemon=True).start()
    return {'job_id': job_id, 'est_sec': est}


def job_status(job_id) -> dict:
    j = _JOBS.get(job_id)
    if not j:
        raise KeyError(job_id)
    return {'job_id': job_id, **j}


def _rv(row, key):
    """sqlite3.Row 安全取值（缺列返回空）。"""
    try:
        return row[key]
    except (IndexError, KeyError):
        return ''


TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'quote_template.xlsx')
TEMPLATE_V2_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'quote_template_v2.xlsx')


def _rv(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return ''


# ================= 报价单 v2.2：ELETRO BELEZA 模板（全字段 + 动态行数 + 动态定金） =================

_NUM = r'(\d+(?:\.\d+)?)'


def _clean(text) -> str:
    """修商家手误：'12..9'→'12.9'（生产库实测见过）。"""
    return re.sub(r'\.{2,}', '.', str(text or ''))


def parse_ctn_spec(text) -> dict:
    """剃须刀箱规文本四件套 → {pcs, nw, gw, meas, dims}，解析不到的键缺省。

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
    if m:
        dims = tuple(float(m.group(i)) for i in (1, 2, 3))
        out['meas'] = f'{_num(dims[0])}*{_num(dims[1])}*{_num(dims[2])}'
        out['dims'] = dims
    return out


def parse_dims(text):
    """卷发棒装箱尺寸 '60*40*40' → (60.0, 40.0, 40.0)，解析不到返回 None。"""
    m = re.search(_NUM + r'\s*[*xX×]\s*' + _NUM + r'\s*[*xX×]\s*' + _NUM, _clean(text))
    return tuple(float(m.group(i)) for i in (1, 2, 3)) if m else None


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


def generate_v2(conn, storage, items, price_adjustment_pct, out_path, deposit_pct: float = 30):
    """ELETRO BELEZA 模板填充：14列全字段，行数=商品数（插行/删空行），
    合计/DEPOSIT/BALANCE 公式按实际行数重写，定金比例动态。"""
    from copy import copy
    from openpyxl.drawing.image import Image as XLImg
    from openpyxl.styles import Alignment

    # 先取数（无效商品剔除后再定行数）
    prows = []
    for item in items:
        t = TEMPLATES.get(item.get('category', ''))
        if not t:
            continue
        p = conn.execute(f'SELECT * FROM {t.table} WHERE id=?',
                         (item['product_id'],)).fetchone()
        if p is None:
            continue
        prows.append((t, p, int(item.get('quantity', 1))))
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

    for i, (t, p, qty) in enumerate(prows):
        r = DATA_START + i
        base = float(str(p['price'] or '0').replace('¥', '').replace(',', '')) or 0
        unit = round(base * (1 + price_adjustment_pct / 100))
        # 物流四件套：剃须刀=箱规文本解析；卷发棒=结构化字段（无重量→留空客户自填）
        if t.key == 'razor':
            ctn = parse_ctn_spec(_rv(p, 'ctn_spec'))
            desc, color = _rv(p, 'description'), _rv(p, 'color')
        else:
            ctn = {}
            if str(_rv(p, 'ctn_qty') or '').strip():
                try:
                    ctn['pcs'] = int(float(_rv(p, 'ctn_qty')))
                except ValueError:
                    pass
            dims = parse_dims(_rv(p, 'ctn_size'))
            if dims:
                ctn['meas'] = f'{_num(dims[0])}*{_num(dims[1])}*{_num(dims[2])}'
                ctn['dims'] = dims
            desc, color = '', ''
        pcs, gw, nw = ctn.get('pcs'), ctn.get('gw'), ctn.get('nw')
        meas, dims = ctn.get('meas'), ctn.get('dims')
        ctns = _ceil_div(qty, pcs) if pcs else None
        tgw = round(ctns * gw, 2) if (ctns is not None and gw) else None
        tcbm = round(ctns * (dims[0] * dims[1] * dims[2]) / 1e6, 3) \
            if (ctns is not None and dims) else None

        ws.cell(r, 1, str(_rv(p, t.dedup_field) or ''))       # A ITEM NO.
        if p['image_main']:                                    # B PHOTO（等比缩放进100pt行）
            try:
                from PIL import Image as PILImage
                with PILImage.open(storage.abs_path(p['image_main'])) as im:
                    w0, h0 = im.size
                ih = 110                                   # 行高100pt≈133px，图110px不越行
                iw = int(w0 * ih / h0) or ih
                if iw > 190:                                # B列宽45字符，超宽压回
                    iw, ih = 190, int(h0 * 190 / w0)
                xi = XLImg(storage.abs_path(p['image_main']))
                xi.width, xi.height = iw, ih
                ws.add_image(xi, f'B{r}')
            except Exception as e:  # noqa: BLE001
                print(f'[quote] 图嵌失败 {p["id"]}: {e}', flush=True)
        c = ws.cell(r, 3, desc)                                # C DESCRIPTION（卷发棒留空）
        c.alignment = Alignment(wrap_text=True, vertical='top')
        ws.cell(r, 4, color)                                   # D COLORS（卷发棒留空）
        ws.cell(r, 5, unit)                                    # E PRICE
        ws.cell(r, 6, qty)                                     # F QUANTITY
        ws.cell(r, 7, f'=ROUND(E{r}*F{r},2)')                  # G TOTAL AMOUNT（公式，改量自动重算）
        ws.cell(r, 8, _num(pcs) if pcs else None)              # H PCS/CTN
        ws.cell(r, 9, ctns)                                    # I CTNS = ⌈量/每箱⌉
        ws.cell(r, 10, _num(gw) if gw else None)               # J G.W/CTN
        ws.cell(r, 11, _num(nw) if nw else None)               # K N.W/CTN
        ws.cell(r, 12, meas)                                   # L MEAS
        ws.cell(r, 13, tgw)                                    # M T.G.W = 箱数×单箱毛重
        if tcbm is not None:
            cell = ws.cell(r, 14, tcbm)                        # N T-CBM = 箱数×单箱体积
            cell.number_format = '0.000'

    # 合计/定金：按内容定位（插删行后位置会动），公式全部重写
    T = next((rr for rr in range(last + 1, last + 12)
              if str(ws.cell(rr, 1).value or '').strip().upper() == 'TOTAL'), None)
    if T is None:
        raise ValueError('模板里找不到 TOTAL 行')
    for col in ('G', 'I', 'M'):
        ws[f'{col}{T}'] = f'=SUM({col}{DATA_START}:{col}{last})'
    ws[f'N{T}'] = f'=SUM(N{DATA_START}:N{last})'
    ws[f'N{T}'].number_format = '0.000'
    D = T + 1                                                   # DEPOSIT 行
    frac = round(deposit_pct / 100, 4)
    ws[f'G{D}'] = f'=ROUND(G{T}*{frac},2)'
    ws[f'G{D + 1}'] = f'=G{T}-G{D}'                             # BALANCE

    wb.save(out_path)
    return out_path
