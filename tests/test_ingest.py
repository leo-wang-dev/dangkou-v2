import json
import sqlite3
import time

import pytest

from catalog import db, ingest
from catalog.storage import LocalStorage
from tests.test_workbook_templates import blowdryer_fixture


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


def test_async_import_creates_ticket_and_calls_back(conn, tmp_path):
    """动态模板阶段：后台建模板工单并回调（stats 带审批入口字段）。"""
    calls = []
    doc_id = ingest.start(conn, LocalStorage(str(tmp_path)), str(blowdryer_fixture(tmp_path)),
                          callback=lambda **kw: calls.append(kw), source_key='vendor-a')
    s = _wait_done(conn, doc_id)
    assert s['status'] == 'ticketed'
    assert calls and calls[0]['stats']['categories'] == ['吹风机']
    assert calls[0]['ticket_id'] and calls[0]['token']
    tk = conn.execute("SELECT * FROM approval_ticket WHERE ticket_type='template_import'").fetchone()
    assert tk is not None and json.loads(tk['payload'])['sheets'][0]['drafts']['new'] == []


import json  # noqa: E402


def test_failed_parse_marks_doc_failed(conn, monkeypatch, tmp_path):
    from catalog import dynamic_import

    def boom(*args, **kwargs):
        raise RuntimeError('解析炸了')
    monkeypatch.setattr(dynamic_import, 'build_template_payload', boom)
    doc_id = ingest.start(conn, LocalStorage(str(tmp_path)), str(blowdryer_fixture(tmp_path)),
                          source_key='vendor-a')
    s = _wait_done(conn, doc_id)
    assert s['status'] == 'failed' and '解析炸了' in s['error']
