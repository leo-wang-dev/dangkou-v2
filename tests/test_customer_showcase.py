from tests.test_wechat_redlines import client
from catalog.main import app


FIELDS = [
    {'key': 'model', 'label': '型号', 'type': 'text', 'visibility': 'public',
     'searchable': True, 'role': 'model', 'required': False},
    {'key': 'price', 'label': '价格', 'type': 'money', 'visibility': 'internal',
     'searchable': False, 'role': 'price', 'required': False},
    {'key': 'remark', 'label': '备注', 'type': 'text', 'visibility': 'internal',
     'searchable': False, 'role': 'note', 'required': False},
]


def test_customer_catalog_and_photos_require_service_auth_and_observe_visibility(client, tmp_path):
    from catalog.storage import LocalStorage
    from tests.conftest import seed_products
    c = app.state.conn
    app.state.storage = LocalStorage(str(tmp_path))
    seed_products(c, [
        {'id': 'shown', 'inner_code': 'shown',
         'data': {'model': 'shown', 'price': 'SECRET_PRICE', 'remark': 'PRIVATE_REMARK'},
         'images': [app.state.storage.save('test_cat', 'shown', 'main.png', b'image-fixture')],
         'cs_visible': 1},
        {'id': 'hidden', 'inner_code': 'hidden',
         'data': {'model': 'hidden', 'price': 'SECRET_PRICE', 'remark': 'PRIVATE_REMARK'},
         'cs_visible': 0},
        {'id': 'pending', 'inner_code': 'pending',
         'data': {'model': 'pending', 'price': 'SECRET_PRICE'}, 'status': 'pending',
         'cs_visible': 1},
    ], key='test_cat', name='测试品类', fields=FIELDS)
    c.commit()
    service_token = client.headers.get('x-service-token')
    client.headers.pop('x-service-token', None)
    assert client.get('/cs/catalog').status_code == 401
    r = client.get('/cs/catalog', headers={'X-Service-Token': service_token})
    assert r.status_code == 200
    assert [p['name'] for p in r.json()['products']] == ['shown']
    assert 'SECRET_PRICE' not in r.text and 'PRIVATE_REMARK' not in r.text
    url = '/cs/catalog/test_cat/shown/photo'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'X-Service-Token': service_token}).content == b'image-fixture'
    c.execute("UPDATE product_dynamic SET cs_visible=0 WHERE id='shown'")
    c.commit()
    assert client.get(url, headers={'X-Service-Token': service_token}).status_code == 404
    assert client.get('/cs/catalog', headers={'X-Service-Token': service_token}).json()['products'] == []
