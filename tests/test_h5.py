from fastapi.testclient import TestClient

from catalog.main import app


def test_index_served_with_views_and_fetch():
    c = TestClient(app)
    r = c.get('/')
    assert r.status_code == 200
    assert '审核' in r.text and '商品' in r.text
    assert 'fetch(' in r.text          # 真实调用后端
    assert '剃须刀' in r.text and '卷发棒' in r.text  # 品类页签
