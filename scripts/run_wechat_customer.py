"""Supervise one customer Bot against the existing WeChat-managed shop database."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config, db, shop_link, merchant_policy
from catalog.wechat_customer import state_dir,secret_path
from catalog.merchant_binding import write_secret


def binding_snapshot():
    """Read the committed binding without running migrations on every heartbeat."""
    conn = db.connect()
    try:
        return shop_link.profile(conn), merchant_policy.read(conn) or {}
    finally:
        conn.close()


def needs_binding_check(child, current_digest, new_digest):
    """A stable live child already validated this exact credential file."""
    return child is None or current_digest != new_digest


def main():
    directory=state_dir();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=open(directory/'supervisor.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    child=None;digest=None;retry_at=0;stopping=False
    ready=directory/'ready'
    def stop_signal(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop_signal);signal.signal(signal.SIGINT,stop_signal)
    def stop_child():
        nonlocal child
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=8)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        child=None;ready.unlink(missing_ok=True)
    try:
        while not stopping:
            status='unbound'
            try:
                data=secret_path().read_bytes();credentials=json.loads(data)
                new_digest=hashlib.sha256(data).hexdigest()
                if child and child.poll() is not None:
                    print(f'[customer-supervisor] 客户Bot子进程退出：{child.returncode}',flush=True)
                    stop_child();retry_at=time.time()+10
                if needs_binding_check(child,digest,new_digest):
                    if digest!=new_digest:
                        stop_child();retry_at=0
                    if child is None and time.time()>=retry_at:
                        p,policy=binding_snapshot()
                        if not policy.get('wechat_managed') or p['tg_bot_id']!=credentials.get('bot_id'):
                            raise ValueError('binding not committed')
                        env=os.environ.copy();env.update(TG_BOT_TOKEN=credentials['bot_token'],MERCHANT_HUB_ENABLED='0',
                            CATALOG_NOTIFY_WORKER='1',MERCHANT_READY_FILE=str(ready))
                        ready.unlink(missing_ok=True)
                        child=subprocess.Popen([sys.executable,'-u','scripts/run_cs_bot.py'],
                            cwd=Path(__file__).resolve().parents[1],env=env)
                        digest=new_digest
                status='running' if child and child.poll() is None and ready.exists() else 'starting'
            except FileNotFoundError:
                stop_child()
            except Exception as exc:
                print(f'[customer-supervisor] 绑定检查失败：{type(exc).__name__}',flush=True)
                stop_child();status='error'
            write_secret(directory/'status.json',{'runtime_status':status,'pid':child.pid if child else None,'updated_at':time.time()})
            time.sleep(1)
    finally:
        stop_child();write_secret(directory/'status.json',{'runtime_status':'stopped','updated_at':time.time()});lock.close()

if __name__=='__main__':main()
