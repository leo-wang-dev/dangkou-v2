"""Bounded, lossless import for sheets that exactly match an approved header.

Ambiguous layouts return None so the existing model parser remains responsible.
"""
from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

from openpyxl import load_workbook

from . import workbook_templates as wbtools


def parse_structured(template: dict, path, output_dir, *, sheet: str = '') -> dict | None:
    wbtools.preflight_workbook(path)
    book = load_workbook(path, data_only=False, read_only=False)
    try:
        visible = [ws for ws in book.worksheets if ws.sheet_state == 'visible' and
                   (not sheet or ws.title == sheet) and
                   (any(wbtools._text(cell.value) for cell in ws._cells.values()) or ws._images)]
        if len(visible) != 1:
            return None
        ws = visible[0]
        fields = template['fields']
        expected = [wbtools._label(f['label']) for f in fields]
        if len(expected) != len(set(expected)) or not expected:
            return None
        matches = []
        for row in range(1, min(ws.max_row, 30) + 1):
            found = [(col, wbtools._label(ws.cell(row, col).value))
                     for col in range(1, min(ws.max_column, 200) + 1)]
            found = [(col, label) for col, label in found if label]
            if [label for _, label in found] == expected:
                matches.append((row, [col for col, _ in found], False))
            elif (len(found) == len(expected) - 1 and [label for _, label in found] == expected[:-1]
                  and fields[-1]['role'] != 'image' and found
                  and not wbtools._text(ws.cell(row, found[-1][0] + 1).value)
                  and any(wbtools._text(ws.cell(r, found[-1][0] + 1).value)
                          for r in range(row + 1, ws.max_row + 1))):
                matches.append((row, [col for col, _ in found] + [found[-1][0] + 1], True))
        if len(matches) != 1:
            return None
        header, columns, unlabeled_tail = matches[0]
        key_col = next((columns[i] for i, f in enumerate(fields)
                        if f['role'] != 'image' and any(term in f['label'] for term in ('名称', '品名'))), None)
        model_col = next((columns[i] for i, f in enumerate(fields) if f['role'] == 'model'), None)
        if key_col is None and model_col is None:
            return None
        model_only = key_col is None
        source_map, _ = wbtools._merged_sources(ws)
        unmapped_rows = {r for (r, c), cell in ws._cells.items()
                         if r > header and c not in columns and wbtools._text(cell.value)}
        if unmapped_rows:
            return None
        starts = []
        for row in range(header + 1, ws.max_row + 1):
            direct = {col: wbtools._text(ws.cell(row, col).value) for col in columns}
            if direct.get(key_col, ''):
                starts.append(row)
            elif direct.get(model_col, ''):
                starts.append(row)
            elif model_only and any(direct[col] for field, col in zip(fields, columns)
                                    if field['role'] != 'image'):
                return None  # Unknown product boundary: let the agent decide.
            elif any(direct.values()) and not starts:
                return None
        if not starts:
            return None
        meaningful_last = max((r for (r, _), cell in ws._cells.items()
                               if wbtools._text(cell.value)), default=header)
        anchors = [getattr(image.anchor, '_from', None) for image in ws._images]
        meaningful_last = max([meaningful_last,
                               *(anchor.row + 1 for anchor in anchors if anchor is not None)])
        for merged in ws.merged_cells.ranges:
            if (wbtools._text(ws.cell(merged.min_row, merged.min_col).value)
                    or any(anchor is not None and merged.min_row <= anchor.row + 1 <= merged.max_row
                           and merged.min_col <= anchor.col + 1 <= merged.max_col for anchor in anchors)):
                meaningful_last = max(meaningful_last, merged.max_row)
        products = []
        inherited_model = ''
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else meaningful_last + 1
            item = {'source_sheet': ws.title, 'source_rows': list(range(start, end)), 'images': []}
            for field, col in zip(fields, columns):
                if field['role'] == 'image':
                    continue
                values = []
                for row in range(start, end):
                    raw = (wbtools._cell_value(ws, source_map, row, col) if row == start
                           else ws.cell(row, col).value)
                    value = wbtools._text(raw)
                    if value and (not values or value != values[-1]):
                        values.append(value)
                item[field['key']] = '\n'.join(values)
                if field['role'] == 'model':
                    if item[field['key']]:
                        inherited_model = item[field['key']]
                    else:
                        item[field['key']] = inherited_model
            products.append(item)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        failures = []
        if unlabeled_tail:
            failures.append({'source_sheet': ws.title, 'source_rows': [header],
                             'reason': f'末列原表头为空，按已审核模板映射为“{fields[-1]["label"]}”，请核对'})
        for field, col in zip(fields, columns):
            if field['role'] != 'image':
                continue
            for row in range(header + 1, ws.max_row + 1):
                value = wbtools._text(ws.cell(row, col).value)
                if not value:
                    continue
                if value != '#NAME?' and 'DISPIMG' not in value.upper():
                    return None
                position = bisect_right(starts, row) - 1
                if position < 0:
                    return None
                failures.append({'source_sheet': ws.title, 'source_rows': [row],
                                 'reason': f'{field["label"]}含未解析的单元格图片引用，请核对该商品图片'})
        for index, image in enumerate(ws._images, 1):
            anchor = getattr(image.anchor, '_from', None)
            if anchor is None:
                return None
            row = anchor.row + 1
            position = bisect_right(starts, row) - 1
            if position < 0:
                failures.append({'source_sheet': ws.title, 'source_rows': [row],
                                 'reason': '表头前或商品行前的图片未归属商品，请核对'})
                continue
            image_columns = {col for field, col in zip(fields, columns) if field['role'] == 'image'}
            if anchor.col + 1 not in image_columns:
                failures.append({'source_sheet': ws.title, 'source_rows': [row],
                                 'reason': '图片锚点不在模板图片列，已按同一商品行归属，请核对'})
            extension = str(getattr(image, 'format', '') or 'png').lower()
            if extension not in {'png', 'jpeg', 'jpg', 'webp', 'gif'}:
                extension = 'png'
            name = f'r{row}_c{anchor.col + 1}_i{index}.{extension}'
            (out / name).write_bytes(image._data())
            products[position]['images'].append(name)
        for item in products:
            item['image_count'] = len(item['images'])
            item['image_main'] = item['images'][0] if item['images'] else ''
        return {'products': products, 'failures': failures, 'vendor': None}
    finally:
        book.close()
