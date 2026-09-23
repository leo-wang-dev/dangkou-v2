from fastapi.testclient import TestClient

from catalog.main import app


def test_index_served_with_views_and_fetch():
    c = TestClient(app)
    r = c.get('/')
    assert r.status_code == 200
    assert '审核' in r.text and '商品' in r.text
    assert 'fetch(' in r.text          # 真实调用后端
    # 品类页签数据驱动：页面不得写死预置分类（空店显示 0 个分类）
    assert "CAT_NAMES = {razor" not in r.text
    assert 'loadCats' in r.text
    # 可观测用卡片按钮控制，不再是表单字段；表单用表头标签并有系统字段过滤
    assert 'toggleProductVisible' in r.text
    assert "['image','sequence','visibility'].includes(f.role)" in r.text
    assert '${f.label||f.col}' in r.text


def test_script_syntax():
    """教训门禁：H5 的 <script> 必须 node --check 通过（曾因串改出孤儿块全页崩）。"""
    import re
    import shutil
    import subprocess
    import tempfile
    import os as _os
    _dir = _os.path.dirname(_os.path.abspath(__file__))
    s = open(_os.path.join(_dir, '..', 'static', 'index.html'), encoding='utf-8').read()
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
