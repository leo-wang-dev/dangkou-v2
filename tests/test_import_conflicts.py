"""Different pending imports must not overwrite a newer approved source snapshot."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from catalog import db, ingest, tickets
from catalog.storage import LocalStorage
from tests.test_ingest import _wait_done


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = str(tmp_path / 'catalog.db')
    conn = db.connect(path)
    db.init_db(conn)
    storage = LocalStorage(str(tmp_path / 'images'))
    monkeypatch.setattr(ingest.agent, 'parse', lambda *args: {'products':[{'model_no':'A','price':'10'}, {'model_no':'B','price':'20'}]})
    def new(source='supplier'):
        did = ingest.start(conn, storage, str(tmp_path/'source.xlsx'), 'razor', source_key=source)
        assert _wait_done(conn,did)['status']=='ticketed'
        return dict(conn.execute("SELECT * FROM approval_ticket WHERE json_extract(payload,'$.doc_id')=?",(did,)).fetchone())
    yield conn, path, new
    conn.close()


def approve(conn, tk):
    return tickets.decide(conn, tk['id'], tk['token'], True)


def test_two_pending_initial_imports_cannot_duplicate(setup):
    conn, _, new=setup
    first, second=new(),new()
    approve(conn,first)
    with pytest.raises(tickets.TicketError,match='变化|过期|冲突'):
        approve(conn,second)
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0]==2
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',(second['id'],)).fetchone()[0]=='pending'


def test_parallel_approvals_only_one_snapshot_wins(setup):
    conn,path,new=setup
    first, second=new(),new()
    def run(tk):
        c=db.connect(path)
        try:
            approve(c,tk)
            return 'approved'
        except tickets.TicketError:
            return 'conflict'
        finally:c.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run,[first,second]))==['approved','conflict']
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0]==2


def test_partial_approval_invalidates_other_ticket_not_its_own_next_row(setup):
    conn,_,new=setup
    first,second=new(),new()
    tickets.decide_row(conn,first['id'],first['token'],'n0',True)
    with pytest.raises(tickets.TicketError):approve(conn,second)
    tickets.decide_row(conn,first['id'],first['token'],'n1',True)
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0]==2


def test_external_edit_blocks_stale_update_and_allows_rejection(setup,monkeypatch):
    conn,_,new=setup
    approve(conn,new())
    monkeypatch.setattr(ingest.agent,'parse',lambda *args:{'products':[{'model_no':'A','price':'11'}]})
    pending=new()
    conn.execute("UPDATE product_razor SET price='99' WHERE model_no='A'")
    conn.commit()
    with pytest.raises(tickets.TicketError):approve(conn,pending)
    assert conn.execute("SELECT price FROM product_razor WHERE model_no='A'").fetchone()[0]=='99'
    tickets.decide(conn,pending['id'],pending['token'],False)


def test_independent_sources_and_reimport_after_conflict(setup):
    conn,_,new=setup
    a,b=new('a'),new('b')
    approve(conn,a);approve(conn,b)
    tk=new('a');approve(conn,tk)
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0]==4


def test_legacy_import_without_snapshot_cannot_bypass_conflict_check(setup):
    conn,_,new=setup
    tk=new()
    payload=json.loads(tk['payload']);payload.pop('source_snapshot')
    conn.execute('UPDATE approval_ticket SET payload=? WHERE id=?',(json.dumps(payload),tk['id']));conn.commit()
    with pytest.raises(tickets.TicketConflict,match='旧导入'):
        approve(conn,tk)
    tickets.decide(conn,tk['id'],tk['token'],False)
