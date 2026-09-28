import threading
import pytest
from catalog import db, dynamic_import, ingest
from catalog.storage import LocalStorage


def test_admission_queues_distinct_jobs_and_drains(tmp_path, monkeypatch):
    conn=db.connect(str(tmp_path/'db')); db.init_db(conn)
    source=tmp_path/'source.xlsx'; source.write_bytes(b'x')
    entered=threading.Event(); release=threading.Event(); active=[]; calls=[]
    def parser(*args,**kw):
        active.append(kw['doc_id']); calls.append(kw['doc_id'])
        assert len(active)==1
        entered.set(); assert release.wait(5)
        active.remove(kw['doc_id'])
        return {'sheets':[{'template':{'name':'test'}}]}
    monkeypatch.setenv('CATALOG_IMPORT_WORKERS','1')
    monkeypatch.setattr(dynamic_import,'build_template_payload',parser)
    storage=LocalStorage(str(tmp_path/'storage'))
    first=ingest.start(conn,storage,str(source),source_key='first')
    assert entered.wait(3)
    other=db.connect(str(tmp_path/'db'))
    try:
        second=ingest.start(other,storage,str(source),source_key='second')
        third=ingest.start(other,storage,str(source),source_key='third')
        assert ingest.status(other,second)['attempt']==0
        assert len(ingest._workers)==1
    finally:
        release.set(); assert ingest.join_workers(10); other.close()
    assert calls==[first,second,third]
    assert all(ingest.status(conn,doc)['status']=='ticketed' for doc in calls)
    conn.close()


def test_cross_process_slot_and_crash_recovery(tmp_path,monkeypatch):
    import subprocess,sys,os
    conn=db.connect(str(tmp_path/'db')); db.init_db(conn)
    source=tmp_path/'source.xlsx'; source.write_bytes(b'x')
    storage=LocalStorage(str(tmp_path/'storage'))
    original=ingest._launch
    monkeypatch.setattr(ingest,'_launch',lambda *a,**kw:None)
    first=ingest.start(conn,storage,str(source),source_key='first')
    second=ingest.start(conn,storage,str(source),source_key='second')
    monkeypatch.setattr(ingest,'_launch',original)
    monkeypatch.setenv('CATALOG_IMPORT_WORKERS','1')
    # Child claims a real SQLite lease and exits as if the process died, no provider/child parser.
    code='from catalog import db,ingest;import sys; c=db.connect(sys.argv[1]);assert ingest._claim(c,int(sys.argv[2]));c.close()'
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'db'),str(first)],check=True,env={**os.environ,'CATALOG_IMPORT_WORKERS':'1'},timeout=5)
    assert ingest._claim(conn,second) is None
    conn.execute('UPDATE import_doc SET lease_until=0 WHERE id=?',(first,));conn.commit()
    monkeypatch.setattr(dynamic_import,'build_template_payload',lambda *a,**kw:{'sheets':[{'template':{'name':'test'}}]})
    assert ingest.recover(conn)==2
    assert ingest.join_workers(10)
    assert ingest.status(conn,first)['status']=='ticketed'
    assert ingest.status(conn,second)['status']=='ticketed'
    conn.close()


@pytest.mark.parametrize('capacity',[1,2])
def test_surviving_parser_keeps_slot_after_document_lease_expiry(tmp_path,monkeypatch,capacity):
    monkeypatch.setenv('CATALOG_IMPORT_WORKERS',str(capacity))
    conn=db.connect(str(tmp_path/'db'));db.init_db(conn)
    source=tmp_path/'source.xlsx';source.write_bytes(b'x')
    entered=threading.Event();release=threading.Event();calls=[]
    def parser(*a,**kw):
        calls.append(kw['doc_id']);entered.set();assert release.wait(5)
        return {'sheets':[{'template':{'name':'test'}}]}
    monkeypatch.setattr(dynamic_import,'build_template_payload',parser)
    doc=ingest.start(conn,LocalStorage(str(tmp_path/'files')),str(source))
    assert entered.wait(3)
    try:
        conn.execute('UPDATE import_doc SET lease_until=0');conn.commit()
        ingest.recover(conn)
        assert ingest.status(conn,doc)['attempt']==1
        assert len(calls)==1
    finally:release.set();assert ingest.join_workers(5);conn.close()


def test_two_tenant_databases_share_actual_execution_capacity(tmp_path,monkeypatch):
    conns=[db.connect(str(tmp_path/f'db{i}')) for i in range(2)]
    for c in conns:db.init_db(c)
    source=tmp_path/'source.xlsx';source.write_bytes(b'x')
    entered=threading.Event();release=threading.Event();calls=[]
    def parser(*a,**kw):
        calls.append(1);entered.set();assert release.wait(5)
        return {'sheets':[{'template':{'name':'test'}}]}
    monkeypatch.setattr(dynamic_import,'build_template_payload',parser)
    storage=LocalStorage(str(tmp_path/'files'))
    ingest.start(conns[0],storage,str(source));assert entered.wait(3)
    try:
        doc=ingest.start(conns[1],storage,str(source))
        assert ingest.status(conns[1],doc)['attempt']==0
        assert len(calls)==1
    finally:release.set();assert ingest.join_workers(5)
    ingest.recover(conns[1]);assert ingest.join_workers(5)
    assert ingest.status(conns[1],doc)['status']=='ticketed'
    for c in conns:c.close()
