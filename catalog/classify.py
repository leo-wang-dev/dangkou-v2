"""导入四分类：新文件/加行=新增；改值=更新；删行=下架。匹配键=品类+型号。"""
from .templates import TEMPLATES


def _val(row, col) -> str:
    """sqlite3.Row 与普通 dict 通吃的取值（缺列=空）。"""
    try:
        v = row[col]
    except (KeyError, IndexError):
        return ''
    return '' if v is None else str(v)


def classify(template_key, drafts, existing_rows):
    t = TEMPLATES[template_key]
    key = t.dedup_field
    existing = {_val(r, key).strip(): r for r in existing_rows if _val(r, key).strip()}
    incoming = {str(d.get(key, '') or '').strip(): d
                for d in drafts if str(d.get(key, '') or '').strip()}
    new = [d for k, d in incoming.items() if k not in existing]
    update = [(existing[k], d) for k, d in incoming.items()
              if k in existing and _differs(existing[k], d, t)]
    delist = [r for k, r in existing.items() if k not in incoming]
    return {'new': new, 'update': update, 'delist': delist}


def _differs(row, draft, t) -> bool:
    return any(_val(row, col) != str(draft.get(col) or '') for col, _ in t.fields)
