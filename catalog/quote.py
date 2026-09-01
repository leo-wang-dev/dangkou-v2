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


def _rv(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return ''


def generate_v2(conn, storage, items, price_adjustment_pct, out_path):
    """纯模板填充：复制源模板 → 清数据行 → 填商品 → 加Qty/Amount列。前14行一字不动。"""
    from openpyxl.drawing.image import Image as XLImg
    from openpyxl.styles import Border, Side
    from .templates import TEMPLATES

    # 1. 加载模板（不copy文件，直接load+save避免权限问题）
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    if 'IN STOCK ITEMS' in wb.sheetnames:
        ws = wb['IN STOCK ITEMS']
        wb.active = wb.index(ws)
        # 删其他Sheet（只留 IN STOCK ITEMS，避免打开时显示错误页）
        for name in list(wb.sheetnames):
            if name != 'IN STOCK ITEMS':
                del wb[name]
    else:
        ws = wb.active

    # 2. 删掉数据行（15行起全删）
    DATA_START = 15
    if ws.max_row >= DATA_START:
        ws.delete_rows(DATA_START, ws.max_row - DATA_START + 1)

    # 3. 删掉数据区的图片（只保留抬头区的）
    ws._images = [img for img in ws._images
                  if img.anchor and img.anchor._from and img.anchor._from.row < DATA_START - 1]

    # 4. 在表头行(14)末尾加 Qty 和 Amount
    HEADER_ROW = 14
    LAST_COL = 6  # 原模板 F 列是 PRICE
    thin = Side(style='thin', color='999999')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ref = ws.cell(HEADER_ROW, LAST_COL)  # PRICE 列头位置
    for j, h in enumerate(['Qty', 'Amount']):
        c = LAST_COL + 1 + j
        cell = ws.cell(HEADER_ROW, c, h)
        from openpyxl.styles import Alignment, Font
        cell.font = Font(bold=True, size=10)
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.column_dimensions['G'].width = 8
    ws.column_dimensions['H'].width = 12

    # 5. 填数据
    r = DATA_START
    total = 0
    for i, item in enumerate(items, 1):
        t = TEMPLATES.get(item.get('category', ''))
        if not t:
            continue
        p = conn.execute(f'SELECT * FROM {t.table} WHERE id=?',
                         (item['product_id'],)).fetchone()
        if p is None:
            continue
        qty = int(item.get('quantity', 1))
        base = float(str(p['price'] or '0').replace('¥','').replace(',','')) if p['price'] else 0
        unit = round(base * (1 + price_adjustment_pct / 100))
        amount = unit * qty
        total += amount

        ws.cell(r, 1, i)                                             # NO.
        ws.cell(r, 2, p[t.dedup_field])                              # ITEM.NO
        if p['image_main']:                                          # PIC
            try:
                xi = XLImg(storage.abs_path(p['image_main']))
                xi.width, xi.height = 50, 50
                ws.add_image(xi, f'C{r}')
            except Exception:
                pass
        ws.cell(r, 4, _rv(p, 'ctn_size') or _rv(p, 'size_mm') or '') # MEAS
        ws.cell(r, 5, _rv(p, 'ctn_qty') or _rv(p, 'ctn_spec') or '') # PCS/CTN
        ws.cell(r, 6, unit)                                            # PRICE
        ws.cell(r, 7, qty)                                             # Qty
        ws.cell(r, 8, amount)                                          # Amount
        ws.cell(r, 8).number_format = '#,##0'
        for c in range(1, 9):
            ws.cell(r, c).border = border
        ws.row_dimensions[r].height = 56
        r += 1

    # 6. 合计行
    from openpyxl.styles import Alignment, Font
    ws.cell(r, 5, 'TOTAL:').alignment = Alignment(horizontal='right')
    ws.cell(r, 5).font = Font(bold=True, size=10)
    ws.cell(r, 8, total)
    ws.cell(r, 8).font = Font(bold=True, size=10)
    ws.cell(r, 8).number_format = '#,##0'
    for c in range(1, 9):
        ws.cell(r, c).border = border

    wb.save(out_path)
    return out_path
