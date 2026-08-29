from fastapi.testclient import TestClient

from catalog.main import app


def test_index_served_with_views_and_fetch():
    c = TestClient(app)
    r = c.get('/')
    assert r.status_code == 200
    assert '审核' in r.text and '商品' in r.text
    assert 'fetch(' in r.text          # 真实调用后端
    assert '剃须刀' in r.text and '卷发棒' in r.text  # 品类页签


def test_script_syntax():
    """教训门禁：H5 的 <script> 必须 node --check 通过（曾因串改出孤儿块全页崩）。"""
    import re
    import shutil
    import subprocess
    import tempfile
    s = open('static/index.html', encoding='utf-8').read()
    js = re.search(r'<script>(.*)</script>', s, re.S).group(1)
    node = shutil.which('node')
    if not node:
        return
    p = tempfile.mktemp(suffix='.js')
    open(p, 'w').write(js)
    try:
        r = subprocess.run([node, '--check', p], capture_output=True, text=True)
        assert r.returncode == 0, f'H5 JS语法错误: {r.stderr[:300]}'
    finally:
        import os
        os.unlink(p)
