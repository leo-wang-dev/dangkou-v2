"""Customer spreadsheet renderer for the /cs/link web export."""
import io
import json
import os
import re
import sqlite3
import openpyxl
from .cs_supplier import normalize
from . import cs_i18n

INTERNAL_NOTE_FIELDS = frozenset({'商品编号', '商品类别'})
# 内部留档口径，对客户导出没有意义（档口归属依据 = bot_context/photo/...）。
HIDDEN_NOTE_FIELDS = frozenset({'商品编号', '商品类别', '档口归属依据'})
# 整列都是这些占位值时视为空列，直接不出现在导出里。
EMPTY_TOKENS = ('', '未拍到', '待补充', '模糊', '模糊（待确认）', '—', 'unknown', 'None')


def _column_useful(key, values):
    if key in HIDDEN_NOTE_FIELDS:
        return False
    return any(str(v).strip() not in EMPTY_TOKENS for v in values)


def render_notes(notes, include_status=False, lang='', conn=None, llm=None, texts=None):
    """客户采购清单 Excel。

    - 只保留有实际内容的列（全空/未拍到/待补充的列删掉，档口归属依据等内部字段不导出）。
    - Fixed labels use local resources; eligible descriptive output uses protected translation.
    - All worksheets are translated together before rendering/cache writes; stored evidence is unchanged.
    """
    lang = cs_i18n.normalize_language(lang)
    notes = list(notes)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    groups = {}
    for note in notes:
        from .note_batches import export_group
        groups.setdefault(export_group(note) if isinstance(note, dict) else ('batch','legacy'), []).append(note)
    prepared = []
    for group in groups.values():
        items, keys = _sheet_data(group)
        card = group[0].get('batch_fields', {}) if isinstance(group[0], dict) else {}
        prepared.append((group, card, items, keys))
    translations = _display_translations(prepared, lang, conn, llm)
    for group, card, items, keys in prepared:
        title = re.sub(r'[\\/*?:\[\]]', '_', str(card.get('档口名称') or cs_i18n.t('shopPending',lang))).strip(" '")[:31] or '采购清单'
        original, number = title, 1
        while title.casefold() in {x.casefold() for x in wb.sheetnames}:
            number += 1
            suffix = f' ({number})'
            title = original[:31-len(suffix)] + suffix
        ws = wb.create_sheet(title)
        _render_sheet(group, ws, card, lang, translations, items, keys)
    if not wb.worksheets:
        wb.create_sheet(cs_i18n.t('myList',lang)[:31])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_data(notes):
    items = [normalize(json.loads(n['fields_json'])) for n in notes]
    # Old durable outbox snapshots may predate the public projection. Strip
    # binding metadata again at render time so retries cannot disclose it.
    for item in items:
        for key in INTERNAL_NOTE_FIELDS:
            item.pop(key, None)
    # 确认状态列按老板 2026-09-24 要求从导出移除（include_status 保留签名兼容旧快照重试）。
    for item in items:
        item.pop('确认状态', None)
        item.pop('起订量', None)   # 字段口径合并进装箱数，历史数据不再出列
    keys = []
    for f in items:                              # 动态字段列（保持出现顺序）
        for k in f:
            if k not in keys:
                keys.append(k)
    keys = [k for k in keys if _column_useful(k, [f.get(k, '') for f in items])]
    return items, keys


def _display_translations(prepared, lang, conn, llm):
    """Plan every worksheet together before any translation cache writes."""
    if lang == 'zh':
        return {}
    sources = ['序号', '商品照片']
    protected = []
    for _, card, items, keys in prepared:
        sources.extend([*keys, *card.keys()])
        protected.extend(str(v) for v in card.values() if v not in EMPTY_TOKENS)
        for item in items:
            for key in keys:
                value = item.get(key, '')
                if cs_i18n.literal_field(key):
                    if value not in EMPTY_TOKENS:
                        protected.append(str(value))
                elif isinstance(value, str) and cs_i18n.display_value(value, lang) == value:
                    sources.append(value)
    sources = list(dict.fromkeys(sources))
    # Standalone renders still show an honest fallback without a provider/cache.
    private = conn is None
    if private:
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
    try:
        # A caller already holding a writer may consume cache only. Normal HTTP
        # exports reach here after read-only ownership/snapshot queries.
        translated = cs_i18n.translate_texts(
            conn, llm, lang, sources, protected=protected, commit=False,
            max_missing=0 if conn.in_transaction or llm is None else None)
        return dict(zip(sources, translated))
    finally:
        if private:
            conn.close()


def _render_sheet(notes, ws, card, lang, translations, items, keys):
    def label(value):
        return translations.get(value, cs_i18n.fixed(value, lang))

    def display(key, value):
        if cs_i18n.literal_field(key):
            return cs_i18n.display_value(value, lang)
        localized = cs_i18n.display_value(value, lang)
        if not isinstance(value, str) or localized != value:
            return localized
        return translations.get(value, value)

    headers = [label(k) for k in ['序号', *keys, '商品照片']]
    rows = [[display(k, f.get(k, '')) for k in keys] for f in items]
    ws.sheet_view.rightToLeft = lang == 'ar'
    from openpyxl.drawing.image import Image as XlImage
    for key, value in card.items():
        ws.append([label(key), value])
    offset = len(card)
    ws.append(headers)
    for i, values in enumerate(rows, 1):
        ws.append([i, *values])
    # User fields, including headers, are literal text, never Excel formulas.
    for cells in ws:
        for cell in cells:
            if cell.data_type == 'f':
                cell.data_type = 's'
    for r_i, n in enumerate(notes, start=2 + offset):      # 商品图嵌入（有图且装了 pillow）
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
    for col in range(2, len(keys) + 2):           # 列宽
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 16
    ws.column_dimensions[openpyxl.utils.get_column_letter(len(keys)+2)].width = 20
    from openpyxl.styles import Alignment
    for cells in ws:
        for cell in cells:
            cell.alignment = Alignment(wrap_text=True, vertical='top', readingOrder=1)
