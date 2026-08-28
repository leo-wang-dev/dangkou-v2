"""报价单：科森 5 列模板（型号/图片/价格/产品规格/起订量），输出可直接转发客户。"""
import re

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
        ws.row_dimensions[r].height = 48
        if p['image_main']:
            try:
                xi = XLImage(storage.abs_path(p['image_main']))
                xi.width, xi.height = 60, 60
                ws.add_image(xi, f'B{r}')
            except Exception as e:  # noqa: BLE001
                print(f'[quote] 图嵌失败 {pid}: {e}', flush=True)
        r += 1
    wb.save(out_path)
    return out_path
