"""Discover category templates and row-aligned images from merchant workbooks."""
from __future__ import annotations

import hashlib
from itertools import islice
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
from pathlib import Path

from openpyxl import load_workbook


def _text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _label(value) -> str:
    return re.sub(r'\s+', ' ', _text(value)).strip()


def _category_key(name: str) -> str:
    normalized = name.strip().casefold()
    return 'cat_' + hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]


def _role(label: str) -> tuple[str, str, str, bool]:
    compact = re.sub(r'\s+', '', label).casefold()
    if compact in {'序号', 'no.', 'no', '编号'}:
        return 'sequence', 'number', 'internal', False
    if any(word in compact for word in ('图片', '照片', 'image', 'picture', 'pic')):
        return 'image', 'image', 'public', True
    if any(word in compact for word in ('产品型号', '商品型号', 'item.no', 'itemno', 'sku', '货号')) or compact == '型号':
        return 'model', 'text', 'public', True
    if any(word in compact for word in ('成本', 'cost')):
        return 'cost', 'money', 'internal', False
    if any(word in compact for word in ('价格', '报价', '单价', '批发价', '出厂价', '拿货价', '售价', 'price')):
        return 'price', 'money', 'internal', False
    if any(word in compact for word in ('链接', '网址', 'url', 'link')):
        return 'note', 'text', 'internal', False
    if '库存' in compact:
        return 'stock', 'number', 'public', True
    if compact in {'备注', '说明', 'note', 'remark'}:
        return 'note', 'text', 'internal', False
    return 'spec', 'text', 'public', any(word in compact for word in ('颜色', '材质', '规格', '名称', '品名'))


def _field_key(label: str, role: str, used: set[str]) -> str:
    preferred = {'sequence': 'sequence', 'model': 'model', 'image': 'image',
                 'stock': 'stock', 'note': 'note'}
    base = preferred.get(role) or 'field_' + hashlib.sha256(label.casefold().encode('utf-8')).hexdigest()[:10]
    key = base
    number = 2
    while key in used:
        key = f'{base}_{number}'
        number += 1
    used.add(key)
    return key


def _header_row(ws) -> int | None:
    best = None
    for row in range(1, min(ws.max_row, 30) + 1):
        labels = [_label(ws.cell(row, col).value) for col in range(1, ws.max_column + 1)]
        nonempty = [value for value in labels if value]
        if len(nonempty) < 2:
            continue
        hints = sum(any(word in value.casefold() for word in
                        ('型号', '货号', 'sku', '图片', '颜色', '价格', '成本', '库存', '规格', '序号'))
                    for value in nonempty)
        score = hints * 1000 + len(nonempty) * 10 - row
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best else None


def _merged_sources(ws) -> tuple[dict[tuple[int, int], tuple[int, int]], dict[tuple[int, int], range]]:
    values = {}
    image_rows = {}
    for merged in ws.merged_cells.ranges:
        top = (merged.min_row, merged.min_col)
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                values[(row, col)] = top
        for col in range(merged.min_col, merged.max_col + 1):
            image_rows[(merged.min_row, col)] = range(merged.min_row, merged.max_row + 1)
    return values, image_rows


def _cell_value(ws, sources, row: int, col: int):
    source = sources.get((row, col), (row, col))
    return ws.cell(*source).value


def _extract_images(ws, sheet_key: str, image_dir: Path | None):
    _, merged_image_rows = _merged_sources(ws)
    by_row: dict[int, list[dict]] = {}
    image_dir = Path(image_dir) if image_dir is not None else None
    if image_dir:
        image_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(ws._images, 1):
        anchor = getattr(image.anchor, '_from', None)
        if anchor is None:
            continue
        row, col = anchor.row + 1, anchor.col + 1
        data = image._data()
        extension = str(getattr(image, 'format', '') or 'png').lower()
        if extension not in {'png', 'jpeg', 'jpg', 'webp', 'gif'}:
            extension = 'png'
        filename = f'{sheet_key}_r{row}_img{index}.{extension}'
        if image_dir:
            (image_dir / filename).write_bytes(data)
            value = filename
        else:
            value = filename
        item = {'path': value, 'sha256': hashlib.sha256(data).hexdigest(),
                'anchor_row': row, 'anchor_col': col}
        target_rows = merged_image_rows.get((row, col), range(row, row + 1))
        for target in target_rows:
            by_row.setdefault(target, []).append(item)
    return by_row, len(ws._images)


def substantive_extents(path):
    """Bound sparse XML before openpyxl expands styled cells/merged ranges."""
    extents = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(n for n in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)):
            if archive.getinfo(name).file_size > 64 * 1024 * 1024:
                raise ValueError('Excel 工作表内容过大，请拆分后导入')
            root = ET.fromstring(archive.read(name))
            cells = root.findall('.//{*}sheetData/{*}row/{*}c')
            if len(cells) > 1000000:
                raise ValueError('Excel 单元格过多，请拆分后导入')
            rows, cols = 1, 1
            for cell in cells:
                if any(child.tag.rsplit('}', 1)[-1] in ('v', 'is', 'f') for child in cell):
                    row, col = coordinate_to_tuple(cell.attrib['r'])
                    rows, cols = max(rows, row), max(cols, col)
            merged_area = 0
            for merge in root.findall('.//{*}mergeCell'):
                bounds = merge.attrib['ref'].split(':')
                a, b = coordinate_to_tuple(bounds[0]), coordinate_to_tuple(bounds[-1])
                merged_area += (b[0]-a[0]+1)*(b[1]-a[1]+1)
                if merged_area > 1000000:
                    raise ValueError('Excel 合并区域过大，请拆分后导入')
            if rows > 20000 or cols > 200:
                raise ValueError('Excel 有效内容超过 20000 行或 200 列，请拆分后导入')
            extents.append((rows, cols))
        for name in archive.namelist():
            if re.fullmatch(r'xl/drawings/drawing\d+\.xml', name):
                root = ET.fromstring(archive.read(name))
                anchors = root.findall('.//{*}from')
                if len(anchors) > 5000:
                    raise ValueError('Excel 图片超过 5000 张，请拆分后导入')
                for anchor in anchors:
                    if int(anchor.find('{*}col').text) >= 200 or int(anchor.find('{*}row').text) >= 20000:
                        raise ValueError('Excel 图片位置超过 20000 行或 200 列，请拆分后导入')
    return extents


def preflight_workbook(path):
    """Check file, archive, sparse cell and drawing bounds before any loader/model."""
    path = Path(path)
    # 91MB/378图的真实目录实测：解析<1s、峰值内存130MB。默认上限放宽到
    # 200MB/750MB（解压上限保持约3.5倍压缩比余量防zip炸弹），需要更紧可环境变量覆盖。
    max_file = int(os.environ.get('CATALOG_IMPORT_MAX_BYTES', 200 * 1024 * 1024))
    max_unpacked = int(os.environ.get('CATALOG_IMPORT_UNPACKED_BYTES', 750 * 1024 * 1024))
    if path.stat().st_size > max_file:
        raise ValueError(f'Excel 文件超过 {max_file // 1048576}MB，请拆分后导入')
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > max_unpacked:
                raise ValueError('Excel 解压内容过大，请删除无关图片或拆分后导入')
    except zipfile.BadZipFile as exc:
        raise ValueError('Excel 文件损坏或不是有效的 xlsx') from exc
    substantive_extents(path)
    with zipfile.ZipFile(path) as archive:
        metadata = ET.fromstring(archive.read('xl/workbook.xml'))
        if len(metadata.findall('.//{*}sheet')) > 20:
            raise ValueError('Excel Sheet 超过 20 个，请拆分后导入')


def discover_workbook(path, image_dir=None, *, include_rows=True, include_images=True) -> list[dict]:
    """Return one template/product draft for every visible worksheet.

    Template-only imports use ``include_rows=False`` and ``include_images=False``:
    the workbook is validated and its schema is discovered without materializing
    product rows or decoding embedded images.  The default remains the original
    full discovery path for compatibility with direct callers.
    """
    path = Path(path)
    preflight_workbook(path)
    # Schema-only discovery streams cells; rows/images require normal mode.
    workbook = load_workbook(path, data_only=False,
                             read_only=not (include_rows or include_images))
    if len(workbook.worksheets) > 20:
        raise ValueError('Excel Sheet 超过 20 个，请拆分后导入')
    drafts = []
    for ws in workbook.worksheets:
        if ws.sheet_state != 'visible':
            continue
        if hasattr(ws, '_cells'):
            for coordinate, cell in list(ws._cells.items()):
                if cell.value is None and (coordinate[0] > 20000 or coordinate[1] > 200):
                    del ws._cells[coordinate]
        else:
            ws._max_column = min(ws.max_column, 200)
            ws._max_row = min(ws.max_row, 20000)
        if len(getattr(ws, '_images', ())) > 5000:
            raise ValueError(f'Sheet“{ws.title}”图片超过 5000 张，请拆分后导入')
        header_row = _header_row(ws)
        if header_row is None:
            continue
        used: set[str] = set()
        fields = []
        for col in range(1, ws.max_column + 1):
            label = _label(ws.cell(header_row, col).value)
            if not label:
                continue
            role, field_type, visibility, searchable = _role(label)
            fields.append({'key': _field_key(label, role, used), 'label': label,
                           'type': field_type, 'required': False,
                           'visibility': visibility, 'searchable': searchable,
                           'role': role, 'source_column': col})
        if not fields:
            continue
        key = _category_key(ws.title)
        observed_rows = set()
        source_max_row = ws.max_row
        if include_rows:
            # Supporting image/merged rows need no image-byte extraction. A
            # product can legitimately span these rows even without text cells.
            observed_rows.update(row for (row, col), cell in ws._cells.items()
                                 if _text(cell.value))
            anchors = {(anchor.row + 1, anchor.col + 1)
                       for image in getattr(ws, '_images', ())
                       if (anchor := getattr(image.anchor, '_from', None)) is not None}
            observed_rows.update(row for row, col in anchors)
            for merged in ws.merged_cells.ranges:
                if _text(ws.cell(merged.min_row, merged.min_col).value) or (merged.min_row, merged.min_col) in anchors:
                    observed_rows.update(range(merged.min_row, merged.max_row + 1))
            source_max_row = max([source_max_row, *observed_rows])
            observed_rows = {row for row in observed_rows if row > header_row}
        sources, _ = _merged_sources(ws) if include_rows else ({}, {})
        if include_images:
            images_by_row, image_count = _extract_images(ws, key, Path(image_dir) if image_dir else None)
        else:
            images_by_row, image_count = {}, 0
        rows = []
        for row_number in range(header_row + 1, ws.max_row + 1) if include_rows else []:
            direct = any(_text(ws.cell(row_number, field['source_column']).value) for field in fields)
            if not direct and row_number not in images_by_row:
                continue
            data = {}
            for field in fields:
                if field['role'] == 'image':
                    continue
                data[field['key']] = _text(_cell_value(ws, sources, row_number, field['source_column']))
            images = [item['path'] for item in images_by_row.get(row_number, [])]
            # Row order is presentation, not product identity. Keeping the row
            # number out prevents a sort/insert from attaching an old ID to a
            # different physical product.
            fingerprint_payload = {'data': data,
                                   'images': [item['sha256'] for item in images_by_row.get(row_number, [])]}
            rows.append({'_rid': f'{key}-r{row_number}', 'data': data,
                         'source_row': row_number, 'images': images,
                         'image_main': images[0] if images else '',
                         'row_fingerprint': hashlib.sha256(
                             json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()})
        title_values = [_label(ws.cell(row, col).value) for row in range(1, header_row)
                        for col in range(1, ws.max_column + 1) if _label(ws.cell(row, col).value)]
        drafts.append({'key': key, 'name': ws.title.strip() or key,
                       'source_sheet': ws.title, 'title': title_values[0] if title_values else '',
                       'header_row': header_row, 'fields': fields, 'rows': rows,
                       'image_count': image_count, 'observed_rows': sorted(observed_rows),
                       'source_max_row': source_max_row})
    workbook.close()
    return drafts


def extract_header_evidence(path, max_rows=8, max_cols=25) -> list:
    """模板表头发现的证据：前几行网格 + 每列图片锚点数 + 顶部合并区。

    表头判断交给 LLM，结构事实（单元格内容/锚点/合并）由代码提供——锚点是
    xlsx drawings 里的确定数据，无标头图片列靠它识别。
    """
    preflight_workbook(path)
    wb = load_workbook(path, read_only=False)
    out = []
    for ws in wb.worksheets:
        if ws.sheet_state != 'visible':
            continue
        grid = []
        for r in range(1, min(ws.max_row, max_rows) + 1):
            grid.append([str(ws.cell(r, c).value).strip()
                         if ws.cell(r, c).value is not None else ''
                         for c in range(1, min(ws.max_column, max_cols) + 1)])
        col_imgs = {}
        for im in getattr(ws, '_images', ()):
            a = getattr(im.anchor, '_from', None)
            if a is not None:
                col_imgs[a.col + 1] = col_imgs.get(a.col + 1, 0) + 1
        merges = [str(m) for m in (ws.merged_cells.ranges if hasattr(ws, 'merged_cells') else [])][:15]
        out.append({'title': ws.title, 'grid': grid,
                    '图片锚点列': col_imgs, '合并区': merges})
    wb.close()
    return out


def extract_xls_header_evidence(path, max_rows=8, max_cols=25) -> list:
    """Read only the old XLS header grid before LibreOffice finishes converting it.

    This provisional evidence intentionally omits drawings.  The caller must
    compare it with converted XLSX evidence before accepting the AI result.
    """
    from python_calamine import CalamineWorkbook

    book = CalamineWorkbook.from_path(str(path))
    out = []
    for title in book.sheet_names:
        sheet = book.get_sheet_by_name(title)
        left = sheet.start[1]
        grid = []
        for row in islice(sheet.iter_rows(), max_rows):
            cells = [''] * left + [_text(value) for value in row]
            grid.append(cells[:max_cols])
        merges = []
        for (start_row, start_col), (end_row, end_col) in sheet.merged_cell_ranges:
            first = f'{get_column_letter(start_col + 1)}{start_row + 1}'
            last = f'{get_column_letter(end_col + 1)}{end_row + 1}'
            merges.append(first if first == last else f'{first}:{last}')
        out.append({'title': title, 'grid': grid, '图片锚点列': {}, '合并区': merges[:15]})
    return out
