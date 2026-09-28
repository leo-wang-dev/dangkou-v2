import json
import os
from pathlib import Path
import subprocess
import sys
from catalog import parser_execution as execution


def test_child_keeps_host_slot_after_supervisor_exits(tmp_path):
    import time
    marker=tmp_path/'release'
    script='''import os,subprocess,sys
from catalog import parser_execution as e
s=e.acquire('tenant-one:1')
code="import pathlib,time,sys;deadline=time.monotonic()+5\\nwhile not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline:time.sleep(.02)"
subprocess.Popen([sys.executable,'-c',code,sys.argv[1]],pass_fds=s.fds,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
os._exit(0)
'''
    result=subprocess.run([sys.executable,'-c',script,str(marker)],capture_output=True,text=True,timeout=3)
    assert result.returncode==0
    try:assert execution.acquire('tenant-two:1') is None
    finally:marker.touch()
    deadline=time.monotonic()+3
    second=None
    while second is None and time.monotonic()<deadline:
        second=execution.acquire('tenant-two:1')
        if second is None:time.sleep(.02)
    assert second
    second.close()


def test_inherited_fd_blocks_until_child_exits(tmp_path):
    slot=execution.acquire('db1:1')
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE,pass_fds=slot.fds)
    slot.close()
    try:assert execution.acquire('db2:1') is None
    finally:child.communicate(timeout=3)
    other=execution.acquire('db2:1');assert other;other.close()


def test_orphan_owned_container_removed_before_slot_reuse(monkeypatch):
    slot=execution.acquire('db1:1')
    marker={'kind':'docker','docker':'fake-docker','name':'dangkou-parser-test','owner':slot.data['owner']}
    slot.child(marker);slot.close()
    calls=[];alive=[True]
    def fake(args,**kwargs):
        calls.append(args)
        if args[1]=='ps':out='id\n' if alive[0] else ''
        elif args[1]=='inspect':out=json.dumps([{'Name':'/'+marker['name'],'Config':{'Labels':{execution.LABEL:marker['owner']}}}])
        elif args[1]=='rm':alive[0]=False;out='id\n'
        return subprocess.CompletedProcess(args,0,stdout=out)
    monkeypatch.setattr(execution.subprocess,'run',fake)
    second=execution.acquire('db2:1')
    assert second and not alive[0]
    assert [c[1] for c in calls]==['ps','inspect','rm','ps']
    second.close()


def test_orphan_absent_or_unknown_daemon_never_releases(monkeypatch):
    slot=execution.acquire('db:1');slot.child({'kind':'docker','docker':'fake','name':'dangkou-parser-test','owner':slot.data['owner']});slot.close()
    for result in [subprocess.CompletedProcess([],0,stdout=''),subprocess.CompletedProcess([],1,stdout='')]:
        monkeypatch.setattr(execution.subprocess,'run',lambda *a,**kw:result)
        assert execution.acquire('other:2') is None


def test_foreign_container_is_not_removed(monkeypatch):
    marker={'kind':'docker','docker':'fake','name':'dangkou-parser-test','owner':'owned'}
    calls=[]
    def fake(args,**kw):
        calls.append(args[1]);return subprocess.CompletedProcess(args,0,stdout='id' if args[1]=='ps' else json.dumps([{'Name':'/'+marker['name'],'Config':{'Labels':{execution.LABEL:'foreign'}}}]))
    monkeypatch.setattr(execution.subprocess,'run',fake)
    assert not execution.reconcile(marker)
    assert calls==['ps','inspect']


def test_uncertain_same_document_cannot_move_to_other_free_slot(monkeypatch):
    monkeypatch.setenv('CATALOG_IMPORT_WORKERS','2')
    first=execution.acquire('other:1');second=execution.acquire('doc:1')
    second.child({'kind':'conversion'});second.close();first.close()
    assert execution.acquire('doc:1') is None
