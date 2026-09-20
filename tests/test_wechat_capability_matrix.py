"""Focused positive/negative checks for the WeChat merchant tool backend."""
from pathlib import Path

from tests.test_release_gates import auth, env


def test_product_photo_upload_accepts_real_image_and_rejects_disguised_bytes(env, tmp_path):
    _, client, _ = env
    from catalog.storage import LocalStorage
    from PIL import Image
    import io
    client.app.state.storage = LocalStorage(str(tmp_path / 'storage'))

    image = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(image, format='PNG')
    accepted = client.post('/upload', headers=auth(), files={
        'file': ('product.png', image.getvalue(), 'image/png')})
    assert accepted.status_code == 200
    assert accepted.json()['path'].endswith('.png')
    served = client.get('/img/' + accepted.json()['path'], headers=auth())
    assert served.status_code == 200
    assert served.headers['content-type'].startswith('image/png')

    rejected = client.post('/upload', headers=auth(), files={
        'file': ('fake.png', b'not-an-image', 'image/png')})
    assert rejected.status_code == 400
    assert '图片' in rejected.text


def test_image_search_rejects_missing_corrupt_and_invalid_top_k(env, tmp_path):
    _, client, _ = env
    missing = client.post('/search', headers=auth(), json={
        'image_path': str(tmp_path / 'missing.png'), 'top_k': 3})
    assert missing.status_code == 404

    corrupt = tmp_path / 'fake.jpg'
    corrupt.write_bytes(b'not-an-image')
    invalid = client.post('/search', headers=auth(), json={
        'image_path': str(corrupt), 'top_k': 3})
    assert invalid.status_code == 400
    assert '图片' in invalid.text

    bad_count = client.post('/search', headers=auth(), json={
        'image_path': str(corrupt), 'top_k': 0})
    assert bad_count.status_code == 422


def test_wechat_management_endpoints_reject_empty_or_unknown_operations(env):
    _, client, _ = env
    assert client.patch('/shop', headers=auth(), json={'changes': {}}).status_code == 400
    assert client.post('/products/not-a-category', headers=auth(), json={
        'changes': {'型号': 'X'}}).status_code == 404
    assert client.patch('/products/curler/missing', headers=auth(), json={
        'changes': {'颜色': '蓝色'}}).status_code == 400
    assert client.post('/quote', headers=auth(), json={'items': []}).status_code == 400


def test_all_wechat_management_endpoints_require_service_identity(env, tmp_path):
    _, client, _ = env
    image = tmp_path / 'product.png'; image.write_bytes(b'not-an-image')
    probes = [
        client.get('/stats'),
        client.get('/shop'),
        client.post('/import', json={'path': str(image)}),
        client.post('/search', json={'image_path': str(image), 'top_k': 3}),
        client.post('/quote', json={'items': [{'category': 'curler', 'product_id': 'p1'}]}),
    ]
    assert all(response.status_code in (401, 403) for response in probes)
