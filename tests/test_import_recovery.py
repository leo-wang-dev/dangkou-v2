import json
import threading

import pytest

from catalog import db, dynamic_import, ingest, notify
from catalog.storage import LocalStorage


def payload(*args, **kw):
    return {'kind': 'template_import', 'phase': 'template', 'doc_id': kw['doc_id'],
            'sheets': [{'template': {'name': 'Test'}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / 'db.sqlite')); db.init_db(conn)
    source = tmp_path / 'input.xlsx'; source.write_bytes(b'test')
    monkeypatch.setattr(dynamic_import, 'build_template_payload', payload)
    yield conn, LocalStorage(str(tmp_path / 'storage')), str(source)
    ingest.join_workers(timeout=5)
    conn.close()


def test_callback_exception_cannot_undo_completion(env):
    conn, storage, source = env
    def broken(**kw):
        raise RuntimeError('callback failed')
    doc = ingest.start(conn, storage, source, broken)
    ingest.join_workers(timeout=5)
    assert ingest.status(conn, doc)['status'] == 'ticketed'
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_finalize_outbox_failure_rolls_back_ticket_and_recovers(env, monkeypatch):
    conn, storage, source = env
    original = notify.push
    def broken(*args, **kwargs):
        raise RuntimeError('outbox unavailable')
    monkeypatch.setattr(notify, 'push', broken)
    doc = ingest.start(conn, storage, source)
    ingest.join_workers(timeout=5)
    assert conn.execute('SELECT count(*) FROM approval_ticket').fetchone()[0] == 0
    monkeypatch.setattr(notify, 'push', original)
    conn.execute('UPDATE import_doc SET lease_until=0'); conn.commit()
    assert ingest.recover(conn) == 1
    ingest.join_workers(timeout=5)
    assert ingest.status(conn, doc)['status'] == 'ticketed'
    assert conn.execute('SELECT count(*) FROM approval_ticket').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_live_lease_deduplicates_cross_connection(env, monkeypatch):
    conn, storage, source = env
    entered = threading.Event(); release = threading.Event()
    def blocked(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return payload(*args, **kwargs)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', blocked)
    doc = ingest.start(conn, storage, source)
    assert entered.wait(5)
    other = db.connect(conn.execute('PRAGMA database_list').fetchone()[2])
    try:
        assert ingest.start(other, storage, source) == doc
        assert ingest.recover(other) == 0
    finally:
        release.set(); ingest.join_workers(timeout=5); other.close()
    assert conn.execute('SELECT count(*) FROM approval_ticket').fetchone()[0] == 1


def test_expired_owner_cannot_finalize_after_reclaim(env, monkeypatch):
    conn, storage, source = env
    entered = threading.Event(); release = threading.Event(); calls = []
    def first_blocks(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set(); assert release.wait(5)
        return payload(*args, **kwargs)
    monkeypatch.setattr(dynamic_import, 'build_template_payload', first_blocks)
    doc = ingest.start(conn, storage, source)
    assert entered.wait(5)
    conn.execute('UPDATE import_doc SET lease_until=0 WHERE id=?', (doc,)); conn.commit()
    assert ingest.recover(conn) == 1
    import time
    deadline = time.monotonic() + 5
    while ingest.status(conn, doc)['status'] == 'parsing' and time.monotonic() < deadline:
        time.sleep(.01)
    release.set(); assert ingest.join_workers(timeout=5)
    assert ingest.status(conn, doc)['attempt'] == 2
    assert conn.execute('SELECT count(*) FROM approval_ticket').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_owned_upload_survives_original_removal_and_notification_failure(env, monkeypatch):
    conn, storage, source = env
    original = notify.push
    monkeypatch.setattr(notify, 'push', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    doc = ingest.start(conn, storage, source)
    ingest.join_workers(timeout=5)
    from pathlib import Path
    Path(source).unlink()
    monkeypatch.setattr(notify, 'push', original)
    conn.execute('UPDATE import_doc SET lease_until=0'); conn.commit()
    ingest.recover(conn); ingest.join_workers(timeout=5)
    assert ingest.status(conn, doc)['status'] == 'ticketed'
    body = conn.execute('SELECT body FROM cs_outbox').fetchone()[0]
    assert 'token' not in body


def test_real_process_loss_can_recover_owned_input(env, monkeypatch):
    import os
    import subprocess
    import sys
    conn, storage, source = env
    monkeypatch.setattr(ingest, '_launch', lambda *a, **kw: None)
    doc = ingest.start(conn, storage, source)
    database = conn.execute('PRAGMA database_list').fetchone()[2]
    code = '''import os, sys
from catalog import db, dynamic_import, ingest
connection = db.connect(sys.argv[1])
def die(*args, **kwargs):
    os._exit(17)
dynamic_import.build_template_payload = die
ingest._run(connection, int(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-c', code, database, str(doc)],
        capture_output=True, timeout=5, env={**os.environ, 'CATALOG_AI_IMPORT': '0'})
    assert result.returncode == 17
    assert ingest.status(conn, doc)['status'] == 'parsing'
    assert conn.execute('SELECT count(*) FROM approval_ticket').fetchone()[0] == 0
    conn.execute('UPDATE import_doc SET lease_until=0'); conn.commit()
    ingest._run(conn, doc)
    assert ingest.status(conn, doc)['status'] == 'ticketed'
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_recovery_reuses_verified_conversion_for_stable_checkpoints(env, monkeypatch):
    conn, storage, source = env
    conversions = []
    monkeypatch.setattr(ingest, '_ensure_xlsx', lambda path: conversions.append(path) or path)
    original = notify.push
    monkeypatch.setattr(notify, 'push', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    doc = ingest.start(conn, storage, source); ingest.join_workers(timeout=5)
    monkeypatch.setattr(notify, 'push', original)
    conn.execute('UPDATE import_doc SET lease_until=0'); conn.commit()
    ingest.recover(conn); ingest.join_workers(timeout=5)
    assert ingest.status(conn, doc)['status'] == 'ticketed'
    assert len(conversions) == 1


def test_template_approval_and_reminder_rollback_together(tmp_path, monkeypatch):
    from catalog import tickets
    from tests.test_workbook_templates import blowdryer_fixture
    conn = db.connect(str(tmp_path / 'approval.sqlite')); db.init_db(conn)
    doc = conn.execute("INSERT INTO import_doc(filename,category,phase) VALUES('a.xlsx','auto','template')").lastrowid
    conn.commit()
    data = dynamic_import.build_template_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work', source_key='source', doc_id=doc)
    tk = tickets.create(conn, 'template_import', None, data)
    original = notify.push
    monkeypatch.setattr(notify, 'push', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('queue unavailable')))
    with pytest.raises(RuntimeError, match='queue unavailable'):
        tickets.decide(conn, tk['id'], tk['token'], True)
    assert conn.execute('SELECT count(*) FROM category_template').fetchone()[0] == 0
    assert conn.execute('SELECT status FROM approval_ticket').fetchone()[0] == 'pending'
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 0
    monkeypatch.setattr(notify, 'push', original)
    tickets.decide(conn, tk['id'], tk['token'], True)
    assert conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1
    conn.close()
