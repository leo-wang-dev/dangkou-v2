import sqlite3
import time

import pytest

from catalog import db, ingest
from catalog.storage import LocalStorage


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


def _wait_done(conn, doc_id, timeout=10):
    for _ in range(timeout * 10):
        s = ingest.status(conn, doc_id)
        if s['status'] in ('ticketed', 'failed'):
            return s
        time.sleep(0.1)
    raise AssertionError('超时未完成')


def test_async_import_creates_ticket_and_calls_back(conn, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ingest.agent, 'parse',
                        lambda cat, p, wd: {'vendor': '厂',
                                            'products': [{'model_no': '8225', 'price': '21.5'}]})
    doc_id = ingest.start(conn, LocalStorage(str(tmp_path)), str(tmp_path / 'f.xlsx'),
                          'razor', callback=lambda **kw: calls.append(kw))
    s = _wait_done(conn, doc_id)
    assert s['status'] == 'ticketed'
    assert calls and calls[0]['stats']['new'] == 1
    tk = conn.execute('SELECT * FROM approval_ticket').fetchone()
    assert tk['ticket_type'] == 'import' and tk['category'] == 'razor'
    assert json.loads(tk['payload'])['drafts']['new'][0]['model_no'] == '8225'


import json  # noqa: E402


def test_failed_parse_marks_doc_failed(conn, monkeypatch, tmp_path):
    def boom(cat, p, wd):
        raise RuntimeError('解析炸了')
    monkeypatch.setattr(ingest.agent, 'parse', boom)
    doc_id = ingest.start(conn, LocalStorage(str(tmp_path)), str(tmp_path / 'f.xlsx'), 'razor')
    s = _wait_done(conn, doc_id)
    assert s['status'] == 'failed' and '解析炸了' in s['error']
