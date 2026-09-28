import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import pytest
from scripts import run_merchant_runtime as runtime


def test_launch_manages_all_roles_and_rolls_back_partial_failure(tmp_path,monkeypatch):
    class Child:
        def __init__(self):self.stopped=False
        def poll(self):return 1 if self.stopped else None
        def terminate(self):self.stopped=True
        def wait(self,timeout=None):return 0
    children=[];commands=[]
    def launch(args,**kwargs):
        commands.append(args)
        if len(commands)==4:raise OSError('sweep launch failed')
        child=Child();children.append(child);return child
    class Session:
        trust_env=True
        def get(self,*a,**kw):return type('Response',(),{'status_code':200})()
    monkeypatch.setattr(runtime.subprocess,'Popen',launch)
    import requests
    monkeypatch.setattr(requests,'Session',Session)
    env={**os.environ,'CATALOG_V2_DB':str(tmp_path/'shop.db'),'CATALOG_CS_SERVICE_TOKEN':'placeholder'}
    with pytest.raises(OSError):runtime.launch(env,19199)
    assert all(c.stopped for c in children)
    joined=' '.join(' '.join(c) for c in commands)
    assert 'run_notifications.py' in joined and 'rebuild_search_index.py' in joined and 'run_guest_sweep.py' in joined


def test_managed_child_exits_when_supervisor_pipe_closes(tmp_path):
    read_fd,write_fd=os.pipe()
    script=tmp_path/'wait.py';ready=tmp_path/'ready'
    script.write_text("import pathlib,time;pathlib.Path("+repr(str(ready))+").write_text('ready');time.sleep(30)")
    child=subprocess.Popen([sys.executable,str(runtime.PROJECT/'scripts/run_managed_child.py'),'--parent-fd',str(read_fd),'--script',str(script)],pass_fds=(read_fd,))
    os.close(read_fd)
    try:
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert ready.exists()
        os.close(write_fd);write_fd=None
        assert child.wait(timeout=3)!=0
    finally:
        if write_fd is not None:os.close(write_fd)
        if child.poll() is None:child.kill();child.wait()


def test_shop_sweep_runs_real_cleanup_and_duplicate_lock(tmp_path,monkeypatch):
    from catalog import db,guest_sessions
    from scripts import run_guest_sweep
    path=tmp_path/'shop.db';photos=tmp_path/'photos';photos.mkdir()
    conn=db.connect(str(path));db.init_db(conn)
    token=guest_sessions.issue(conn);conn.execute('UPDATE guest_sessions SET expires_at=0');conn.commit()
    run_guest_sweep.run(conn,str(photos),rounds=1)
    assert not conn.execute('SELECT 1 FROM guest_sessions').fetchone()
    conn.close()
    with run_guest_sweep.ownership(str(path)):
        with pytest.raises(BlockingIOError):
            with run_guest_sweep.ownership(str(path)):pass


def test_supervisor_restarts_whole_group_after_role_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('MERCHANT_HUB_DB',str(tmp_path/'hub.db'))
    monkeypatch.setattr(runtime,'root',lambda:tmp_path)
    conn=runtime.hub.connect()
    conn.execute("INSERT INTO merchant(id,owner,state) VALUES('one','owner','catalog_ready')");conn.commit();conn.close()
    clock=[0];handlers={};groups=[]
    class Child:
        def __init__(self):self.dead=False
        def poll(self):return 1 if self.dead else None
        def terminate(self):self.dead=True
        def wait(self,timeout=None):return 0
    def launch(*a,**kw):
        group=[Child() for _ in range(4)];groups.append(group);return group
    monkeypatch.setattr(runtime,'provision',lambda *a:({},19199,False))
    monkeypatch.setattr(runtime,'launch',launch)
    monkeypatch.setattr(runtime.signal,'signal',lambda sig,fn:handlers.setdefault(sig,fn))
    monkeypatch.setattr(runtime.time,'time',lambda:clock[0])
    def tick(*a):
        clock[0]+=31
        if len(groups)==1:groups[0][2].dead=True
        if len(groups)==2:handlers[runtime.signal.SIGTERM]()
        assert clock[0]<200
    monkeypatch.setattr(runtime.time,'sleep',tick)
    runtime.main()
    assert len(groups)==2
    assert all(child.dead for group in groups for child in group)
    conn=runtime.hub.connect()
    assert conn.execute("SELECT runtime_status FROM merchant WHERE id='one'").fetchone()[0]=='stopped'
    conn.close()


def test_runtime_group_lock_blocks_duplicate_and_releases_on_stop(tmp_path,monkeypatch):
    class Child:
        def poll(self):return None
        def terminate(self):pass
        def wait(self,timeout=None):return 0
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *a,**kw:Child())
    import requests
    class Session:
        def get(self,*a,**kw):return type('Response',(),{'status_code':200})()
    monkeypatch.setattr(requests,'Session',Session)
    env={**os.environ,'CATALOG_V2_DB':str(tmp_path/'shop.db'),'CATALOG_CS_SERVICE_TOKEN':'placeholder'}
    first=runtime.launch(env,19199)
    try:
        with pytest.raises(BlockingIOError):runtime.launch(env,19199)
    finally:runtime.stop(first)
    second=runtime.launch(env,19199);runtime.stop(second)
