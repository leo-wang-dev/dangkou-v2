"""Read-only customer catalog backed by the merchant service, never a snapshot."""
import base64
import json
import os
from urllib.parse import quote

import requests

from . import price_policy, shop_link
from .templates import TEMPLATES


class CatalogUnavailable(RuntimeError):
    pass


class PhotoUnavailable(CatalogUnavailable):
    """The requested product/photo is no longer eligible for customer delivery."""
    pass


def public_product(t, row):
    specs = {label: str(row[col]) for col, label in t.fields
             if col not in ('price', 'remark') and row[col]
             and price_policy.public_spec_allowed(label, row[col])}
    try:
        images = json.loads(row['images'] or '[]')
    except (TypeError, ValueError):
        images = []
    name = str(row[t.dedup_field] or t.name + '商品')
    if not price_policy.public_spec_allowed('型号', name):
        name = t.name + '商品'
    return {'id': row['id'], '_category': t.key, 'category_name': t.name,
            'name': name,
            'status': row['status'], 'cs_visible': row['cs_visible'], 'specs': specs,
            'image_main': row['image_main'], 'images': images}


def local_catalog(conn):
    from . import dynamic_catalog
    dynamic = [product for template in dynamic_catalog.list_templates(conn)
               if template['storage'] == 'dynamic'
               for product in dynamic_catalog.list_products(conn, template['key'], public_only=True)]
    return {'shop_id': shop_link.profile(conn)['shop_id'], 'products': [
        public_product(t, row) for t in TEMPLATES.values()
        for row in conn.execute(f"SELECT * FROM {t.table} WHERE status='approved' AND cs_visible=1")
    ] + dynamic}


def remote(conn, path, body=None):
    base = os.environ.get('CATALOG_CS_API_URL', '').rstrip('/')
    token = os.environ.get('CATALOG_CS_SERVICE_TOKEN', '')
    if not base or not token:
        raise CatalogUnavailable('客户查询接口未配置')
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.request('POST' if body is not None else 'GET', base + path,
                                       headers={'X-Service-Token': token}, json=body,
                                       timeout=(5, 90 if body is not None else 15))
            response.raise_for_status()
            result = response.json()
        if result.get('shop_id') != shop_link.profile(conn)['shop_id']:
            raise ValueError('shop identity mismatch')
        return result
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise CatalogUnavailable('档口查询服务暂不可用') from None


def products(conn):
    if os.environ.get('CATALOG_CS_API_URL'):
        return remote(conn, '/cs/catalog')['products']
    return local_catalog(conn)['products']


def candidates(conn, fields, photo):
    return remote(conn, '/cs/catalog/search', {
        'fields': fields, 'image_base64': base64.b64encode(photo).decode(),
    })['candidates']


def photo_bytes(conn, product):
    """Read a currently visible product image through the private merchant link."""
    category, product_id = product['_category'], product['id']
    if os.environ.get('CATALOG_CS_API_URL'):
        base = os.environ.get('CATALOG_CS_API_URL', '').rstrip('/')
        token = os.environ.get('CATALOG_CS_SERVICE_TOKEN', '')
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    f'{base}/cs/catalog/{quote(category, safe="")}/{quote(product_id, safe="")}/photo',
                    headers={'X-Service-Token': token}, timeout=(5, 30))
                if response.status_code == 404:
                    raise PhotoUnavailable('商品图片已下架或不存在')
                response.raise_for_status()
                if response.headers.get('X-Shop-Id') != shop_link.profile(conn)['shop_id']:
                    raise ValueError('shop identity mismatch')
                return response.headers.get('X-Filename') or 'product.jpg', response.content
        except PhotoUnavailable:
            raise
        except (requests.RequestException, ValueError, AttributeError):
            raise CatalogUnavailable('商品图片暂不可用') from None
    live = next((value for value in local_catalog(conn)['products']
                 if value['_category'] == category and value['id'] == product_id), None)
    if not live or not live.get('image_main'):
        raise PhotoUnavailable('商品图片已下架或不存在')
    from .storage import LocalStorage
    base = os.environ.get('CATALOG_V2_IMG')
    if not base:
        from . import config
        base = config.IMG_DIR
    try:
        return os.path.basename(live['image_main']) or 'product.jpg', LocalStorage(base).read(live['image_main'])
    except (OSError, ValueError):
        raise PhotoUnavailable('商品图片已下架或不存在') from None
