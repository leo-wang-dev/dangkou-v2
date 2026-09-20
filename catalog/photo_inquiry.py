"""Suggest catalog candidates; only an explicit customer selection can become a quote."""
import json
import os
import re
from . import config, search
from .templates import TEMPLATES


def product(conn, category, product_id):
    t = TEMPLATES.get(category)
    if not t:
        from . import dynamic_catalog
        try:
            return next((value for value in dynamic_catalog.list_products(
                conn, category, public_only=True) if value['id'] == product_id), None)
        except KeyError:
            return None
    row = conn.execute(f"SELECT * FROM {t.table} WHERE id=? AND status='approved'", (product_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), '_category': category, 'name': str(row[t.dedup_field] or row['inner_code'])}


def candidates(conn, fields, photo):
    if os.environ.get('CATALOG_CS_API_URL'):
        from . import customer_catalog
        try:
            return customer_catalog.candidates(conn, fields, photo)
        except customer_catalog.CatalogUnavailable:
            return []  # Keep photo bookkeeping; never substitute an old catalog.
    return local_candidates(conn, fields, photo)


def local_candidates(conn, fields, photo):
    # Extracted price/carton numbers never participate in catalog matching or pricing.
    names = [str(item.get('型号或品名') or item.get('型号') or '') for item in fields]
    found = []
    from . import customer_catalog
    for value in customer_catalog.local_catalog(conn)['products']:
        name = str(value.get('name') or '')
        if name and any(re.search(r'(?<![A-Za-z0-9_-])'+re.escape(name)+r'(?![A-Za-z0-9_-])', n, re.I) for n in names):
            found.append({'category': value['_category'], 'product_id': value['id'], 'name': name})
    # Image similarity supplies candidates, never proof that two products are identical.
    if not found and config.BAILIAN_API_KEY and conn.execute('SELECT 1 FROM embedding LIMIT 1').fetchone():
        try:
            vec = search.embed_image(photo)
            min_score = float(os.environ.get('BAILIAN_MM_MIN_SCORE', '0.45'))
            for hit in search.query(conn, vec, top_k=10):
                if float(hit.get('score', -1)) < min_score:
                    continue
                p = product(conn, hit['category'], hit['product_id'])
                if p and p['cs_visible']:
                    found.append({'category':hit['category'], 'product_id':p['id'], 'name':p['name']})
        except Exception:
            # Photo bookkeeping remains usable when the retrieval provider is unavailable.
            return []
    return found[:3]


def save(conn, customer_id, found):
    conn.execute("INSERT INTO cs_photo_candidates(customer_id,candidates,expires_at) VALUES(?,?,datetime('now','+30 minutes')) "
                 "ON CONFLICT(customer_id) DO UPDATE SET candidates=excluded.candidates,expires_at=excluded.expires_at",
                 (customer_id, json.dumps(found, ensure_ascii=False)))


def selection(conn, customer_id, number):
    row = conn.execute("SELECT candidates FROM cs_photo_candidates WHERE customer_id=? AND expires_at>datetime('now')", (customer_id,)).fetchone()
    if not row:
        return None
    found = json.loads(row['candidates'])
    if not 1 <= number <= len(found):
        return None
    selected = found[number-1]
    if os.environ.get('CATALOG_CS_API_URL'):
        from . import customer_catalog
        return next((p for p in customer_catalog.products(conn)
                     if p['id'] == selected['product_id'] and p['_category'] == selected['category']), None)
    return product(conn, selected['category'], selected['product_id'])
