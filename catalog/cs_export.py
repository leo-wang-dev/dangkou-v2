"""Shared customer spreadsheet renderer for web and Telegram attachments."""
import io
import json
import os
import openpyxl
from .cs_supplier import normalize

INTERNAL_NOTE_FIELDS = frozenset({'商品编号', '商品类别'})


def render_notes(notes, include_status=False):
    items = [normalize(json.loads(n['fields_json'])) for n in notes]
    # Old durable outbox snapshots may predate the public projection. Strip
    # binding metadata again at render time so retries cannot disclose it.
    for item in items:
        for key in INTERNAL_NOTE_FIELDS:
            item.pop(key, None)
    if include_status:
        for item, note in zip(items, notes):
            item['确认状态'] = '待确认' if note['status'] == 'draft' else '已确认'
    keys = []
    for f in items:                              # 动态字段列（保持出现顺序）
        for k in f:
            if k not in keys:
                keys.append(k)
    wb = openpyxl.Workbook()
    ws = wb.active
    from openpyxl.drawing.image import Image as XlImage
    ws.append(['序号', *keys, '商品照片'])
    for i, f in enumerate(items, 1):
        ws.append([i, *[str(f.get(k, '')) for k in keys]])
    # User fields, including headers, are literal text, never Excel formulas.
    for cells in ws:
        for cell in cells:
            if cell.data_type == 'f':
                cell.data_type = 's'
    for r_i, n in enumerate(notes, start=2):      # 商品图嵌入（有图且装了 pillow）
        if not (n['photo'] and os.path.exists(n['photo'])):
            continue
        try:
            img = XlImage(n['photo'])
            scale = min(120 / img.width, 120 / img.height)
            img.width, img.height = img.width * scale, img.height * scale
            ws.add_image(img, f'{openpyxl.utils.get_column_letter(len(keys)+2)}{r_i}')
            ws.row_dimensions[r_i].height = 96
        except Exception:                         # noqa: BLE001 pillow 缺失/图损坏 → 跳过
            pass
    for col, k in enumerate(keys, start=2):      # 列宽
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 16
    ws.column_dimensions[openpyxl.utils.get_column_letter(len(keys)+2)].width = 20
    from openpyxl.styles import Alignment
    for cells in ws:
        for cell in cells:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
