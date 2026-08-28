"""图向量检索：百炼多模态嵌入 + 余弦 + 换一批（exclude）。text_vec 为预留优化位。"""
import base64
import math
import struct

import requests

from . import config
from .templates import TEMPLATES

_MM_URL = (config.BAILIAN_BASE_URL.replace('/compatible-mode/v1', '')
           + '/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding')


def embed_image(data: bytes) -> list[float]:
    body = {'model': 'multimodal-embedding-one-peace-v1',
            'input': {'contents': [{'image': base64.b64encode(data).decode()}]}}
    r = requests.post(_MM_URL, json=body, timeout=60,
                      headers={'Authorization': f'Bearer {config.BAILIAN_API_KEY}',
                               'Content-Type': 'application/json'})
    r.raise_for_status()
    return r.json()['output']['embeddings'][0]['embedding']


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def query(conn, vec, category=None, top_k=5, exclude=()):
    exclude = set(exclude)
    hits = []
    for row in conn.execute('SELECT product_id, category, image_path, vec FROM embedding'):
        if row['product_id'] in exclude:
            continue
        if category and row['category'] != category:
            continue
        ev = struct.unpack(f'{len(row["vec"]) // 4}f', row['vec'])
        hits.append((_cos(vec, ev), row))
    hits.sort(key=lambda x: -x[0])
    out = []
    for score, row in hits[:top_k]:
        t = TEMPLATES[row['category']]
        p = conn.execute(f'SELECT * FROM {t.table} WHERE id=?',
                         (row['product_id'],)).fetchone()
        if p is None or p['status'] == 'delisted':
            continue
        out.append({'product_id': row['product_id'], 'category': row['category'],
                    'score': round(score, 4),
                    'fields': {l: p[c] for c, l in t.fields},
                    'inner_code': p['inner_code'], 'image': row['image_path']})
    return out


def reindex(conn, storage, category) -> int:
    """为有主图但无向量的商品补嵌入（审批通过/图片落位后调用）。幂等。"""
    t = TEMPLATES[category]
    n = 0
    for p in conn.execute(f"SELECT id, image_main FROM {t.table} "
                          f"WHERE image_main != '' AND status != 'delisted'").fetchall():
        if conn.execute('SELECT 1 FROM embedding WHERE product_id=?',
                        (p['id'],)).fetchone():
            continue
        try:
            vec = embed_image(storage.read(p['image_main']))
        except Exception as e:  # noqa: BLE001
            print(f'[search] 嵌入失败 {p["id"]}: {e}', flush=True)
            continue
        conn.execute('INSERT INTO embedding(product_id, category, image_path, vec) '
                     'VALUES(?,?,?,?)',
                     (p['id'], category, p['image_main'],
                      struct.pack(f'{len(vec)}f', *vec)))
        n += 1
    conn.commit()
    return n
