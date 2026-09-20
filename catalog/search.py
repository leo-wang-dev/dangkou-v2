"""图向量检索：百炼多模态嵌入 + 余弦 + 换一批（exclude）。text_vec 为预留优化位。"""
import base64
import json
import os
import math
import struct

import requests

from . import config
from .templates import TEMPLATES

_MM_URL = (config.BAILIAN_BASE_URL.replace('/compatible-mode/v1', '')
           + '/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding')


def embed_image(data: bytes, filename: str = 'img.jpeg') -> list[float]:
    model = os.environ.get('BAILIAN_MM_MODEL', 'qwen3-vl-embedding')
    dim = int(os.environ.get('BAILIAN_MM_DIM', '1024'))
    fmt = 'png' if filename.lower().endswith('.png') else 'jpeg'
    b64 = base64.b64encode(data).decode()
    body = {'model': model,
            'input': {'contents': [{'image': f'data:image/{fmt};base64,{b64}'}]},
            'parameters': {'dimension': dim}}
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
    for score, row in hits:
        t = TEMPLATES.get(row['category'])
        if t:
            p = conn.execute(f'SELECT * FROM {t.table} WHERE id=?',
                             (row['product_id'],)).fetchone()
            fields = {label: p[col] for col, label in t.fields} if p else {}
        else:
            from . import dynamic_catalog
            try:
                template = dynamic_catalog.get_template(conn, row['category'])
            except KeyError:
                continue
            p = conn.execute('SELECT * FROM product_dynamic WHERE id=? AND category_key=?',
                             (row['product_id'], row['category'])).fetchone()
            data = json.loads(p['data_json'] or '{}') if p else {}
            fields = {field['label']: data.get(field['key'], '') for field in template['fields']}
        if p is None or p['status'] == 'delisted':
            continue
        if any(hit['product_id'] == row['product_id'] for hit in out):
            continue
        out.append({'product_id': row['product_id'], 'category': row['category'],
                    'score': round(score, 4),
                    'fields': fields,
                    'inner_code': p['inner_code'], 'image': row['image_path']})
        if len(out) >= top_k:
            break
    return out


def reindex(conn, storage, category) -> int:
    """为有主图但无向量的商品补嵌入（审批通过/图片落位后调用）。幂等。"""
    t = TEMPLATES.get(category)
    n = 0
    if t:
        table = t.table
        products = conn.execute(f"SELECT id, image_main FROM {table} "
                                f"WHERE image_main != '' AND status != 'delisted'").fetchall()
    else:
        from . import dynamic_catalog
        dynamic_catalog.get_template(conn, category)
        table = 'product_dynamic'
        products = conn.execute(
            "SELECT id,image_main FROM product_dynamic WHERE category_key=? "
            "AND image_main != '' AND status != 'delisted'", (category,)).fetchall()
    for p in products:
        if conn.execute('SELECT 1 FROM embedding WHERE product_id=?',
                        (p['id'],)).fetchone():
            continue
        retry = conn.execute("SELECT *,next_attempt_at>datetime('now') AS waiting FROM embedding_retry WHERE product_id=? AND category=?", (p['id'], category)).fetchone()
        if retry and retry['waiting']:
            continue
        try:
            vec = embed_image(storage.read(p['image_main']))
        except Exception as e:  # noqa: BLE001
            attempts = retry['attempts'] if retry else 0
            delay = min(1800, 30 * 2 ** min(attempts,6))
            conn.execute("INSERT INTO embedding_retry(product_id,category,attempts,next_attempt_at,last_error) VALUES(?,?,1,datetime('now',?),?) ON CONFLICT(product_id,category) DO UPDATE SET attempts=attempts+1,next_attempt_at=excluded.next_attempt_at,last_error=excluded.last_error", (p['id'],category,f'+{delay} seconds',type(e).__name__))
            conn.commit()
            print(f'[search] 嵌入失败 {p["id"]}: {type(e).__name__}，已安排重试', flush=True)
            continue
        latest = conn.execute(f'SELECT status,image_main FROM {table} WHERE id=?',(p['id'],)).fetchone()
        if not latest or latest['status'] != 'approved' or latest['image_main'] != p['image_main']:
            continue
        conn.execute('INSERT OR IGNORE INTO embedding(product_id, category, image_path, vec) '
                     'VALUES(?,?,?,?)',
                     (p['id'], category, p['image_main'],
                      struct.pack(f'{len(vec)}f', *vec)))
        conn.execute('DELETE FROM embedding_retry WHERE product_id=? AND category=?',(p['id'],category))
        conn.commit()
        n += 1
    conn.commit()
    return n
