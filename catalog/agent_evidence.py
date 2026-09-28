"""Small-workbook evidence for the semantic product parser.

Cells and image anchors are facts; deciding logical products remains the agent's job.
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from . import workbook_templates


def prepare(path, work_dir, *, sheet: str = '') -> dict | None:
    workbook_templates.preflight_workbook(path)
    book = load_workbook(path, data_only=False, read_only=False)
    try:
        sheets = []
        pending_images = []
        for ws in book.worksheets:
            if ws.sheet_state != 'visible' or (sheet and ws.title != sheet):
                continue
            by_row = {}
            for (row, col), cell in ws._cells.items():
                value = workbook_templates._text(cell.value)
                if value:
                    by_row.setdefault(row, []).append([col, value])
            if len(by_row) > 300 or len(ws._images) > 100:
                return None
            rows = [{'row': row, 'cells': sorted(cells)} for row, cells in sorted(by_row.items())]
            images = []
            for index, image in enumerate(ws._images, 1):
                anchor = getattr(image.anchor, '_from', None)
                if anchor is None:
                    return None
                ext = str(getattr(image, 'format', '') or 'png').lower()
                if ext not in {'png', 'jpeg', 'jpg', 'webp', 'gif'}:
                    ext = 'png'
                name = f'evidence_s{len(sheets)+1}_r{anchor.row+1}_c{anchor.col+1}_i{index}.{ext}'
                images.append({'row': anchor.row+1, 'col': anchor.col+1, 'name': name})
                pending_images.append((name, image))
            sheets.append({'name': ws.title, 'rows': rows, 'images': images,
                           'merges': [str(merged) for merged in ws.merged_cells.ranges]})
        evidence = {'sheets': sheets}
        if not sheets or len(json.dumps(evidence, ensure_ascii=False).encode('utf-8')) > 60000:
            return None
        output = Path(work_dir)
        output.mkdir(parents=True, exist_ok=True)
        for name, image in pending_images:
            (output / name).write_bytes(image._data())
        return evidence
    finally:
        book.close()
