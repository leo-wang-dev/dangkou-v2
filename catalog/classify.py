"""导入四分类：新文件/加行=新增；改值=更新；删行=下架。匹配键=品类+型号。"""
import hashlib
import json
from pathlib import Path

from .templates import TEMPLATES


def _val(row, col) -> str:
    """sqlite3.Row 与普通 dict 通吃的取值（缺列=空）。"""
    try:
        v = row[col]
    except (KeyError, IndexError):
        return ''
    return '' if v is None else str(v)


def classify(template_key, drafts, existing_rows, draft_image_root=None, existing_image_root=None):
    t = TEMPLATES[template_key]
    key = t.dedup_field
    def differs(row, draft):
        if _differs(row, draft, t):
            return True
        if draft_image_root is None or existing_image_root is None or not any(k in draft for k in ("images", "image_main")):
            return False
        return _image_hashes(row, existing_image_root) != _image_hashes(draft, draft_image_root)
    incoming = [d for d in drafts if str(d.get(key, '') or '').strip()]
    # Match repeated model variants one-to-one; never collapse incoming rows.
    unused = list(existing_rows)
    new, update = [], []
    for d in incoming:
        matches = [r for r in unused if _val(r, key).strip() == str(d[key]).strip()]
        exact = next((r for r in matches if not differs(r, d)), None)
        match = exact if exact is not None else next((r for r in matches if _val(r, 'color') == str(d.get('color') or '')), None)
        if match is None and len(matches) == 1 and sum(str(x[key]).strip() == str(d[key]).strip() for x in incoming) == 1:
            match = matches[0]
        if match is None:
            new.append(d)
        else:
            unused.remove(match)
            if differs(match, d):
                update.append((match, d))
    delist = unused
    return {'new': new, 'update': update, 'delist': delist}


def _differs(row, draft, t) -> bool:
    return any(_val(row, col) != str(draft.get(col) or '') for col, _ in t.fields)


def _image_hashes(row, root):
    values = dict(row)
    images = values.get('images')
    if isinstance(images, str):
        images = json.loads(images or '[]')
    if images is None:
        images = [values['image_main']] if values.get('image_main') else []
    hashes = []
    base = Path(root).resolve()
    for name in images:
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('导入图片缺失或不在允许目录，请重新上传')
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return hashes
