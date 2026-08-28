import os

from catalog.storage import LocalStorage


def test_save_and_read_roundtrip(tmp_path):
    s = LocalStorage(str(tmp_path))
    rel = s.save('razor', 'id1', 'main.png', b'PNGDATA')
    assert rel == os.path.join('razor', 'id1', 'main.png')
    assert open(s.abs_path(rel), 'rb').read() == b'PNGDATA'
    assert s.read(rel) == b'PNGDATA'
