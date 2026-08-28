import re

from catalog import inner_code


def test_format_and_alphabet_and_uniqueness():
    seen = set()
    for _ in range(500):
        c = inner_code.gen()
        assert re.fullmatch(r'KS-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}', c)
        seen.add(c)
    assert len(seen) >= 495
