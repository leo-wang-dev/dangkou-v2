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


def generate_v2(conn, storage, items, price_adjustment_pct, out_path):
    """通用模板 v2：多商品 + 各自数量 + 百分比调整 + Qty/Amount。

    items: [{category, product_id, quantity}, ...]
    price_adjustment_pct: 正=上浮, 负=下浮 (如 3 = +3%, -5 = -5%)
    """
    from .templates import TEMPLATES
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Quotation'
    headers = ['NO.', 'ITEM.NO', 'PIC', 'MEAS', 'PCS/CTN', 'PRICE', 'Qty', 'Amount']
    ws.append(headers)
    for c, w in [(1, 5), (2, 16), (3, 14), (4, 16), (5, 9), (6, 10), (7, 8), (8, 12)]:
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.row_dimensions[1].font = Font(bold=True)

    r = 2
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
        base_price = float(str(p['price'] or '0').replace('¥', '').replace(',', '')) if p['price'] else 0
        unit = round(base_price * (1 + price_adjustment_pct / 100))
        amount = unit * qty
        total += amount

        ws.cell(r, 1, i)
        ws.cell(r, 2, p[t.dedup_field])
        if p['image_main']:
            try:
                from openpyxl.drawing.image import Image as XLImg
                xi = XLImg(storage.abs_path(p['image_main']))
                xi.width, xi.height = 52, 52
                ws.add_image(xi, f'C{r}')
            except Exception:
                pass
        ws.cell(r, 4, _rv(p, 'ctn_size') or _rv(p, 'size_mm') or '')
        ws.cell(r, 5, _rv(p, 'ctn_qty') or _rv(p, 'ctn_spec') or '')
        ws.cell(r, 6, unit)
        ws.cell(r, 7, qty)
        ws.cell(r, 8, amount)
        ws.row_dimensions[r].height = 42
        r += 1

    # 合计行
    ws.cell(r, 7, 'Total:')
    ws.cell(r, 8, total)
    ws.cell(r, 7).font = Font(bold=True)
    ws.cell(r, 8).font = Font(bold=True)

    wb.save(out_path)
    return out_path
