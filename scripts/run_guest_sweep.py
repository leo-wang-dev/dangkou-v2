"""Periodic shop guest cleanup, owned once per actual tenant database."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config,db,guest_sessions
import os


@contextmanager
def ownership(path):
    with open(path+'.sweep.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def run(conn,photos,rounds=None):
    count=0
    while rounds is None or count<rounds:
        guest_sessions.sweep(conn,'shop',photos)
        count+=1
        if rounds is None or count<rounds:time.sleep(900)


def main():
    with ownership(config.DB_PATH):
        conn=db.connect();db.init_db(conn)
        try:run(conn,os.environ['CATALOG_CS_PHOTOS'])
        finally:conn.close()


if __name__=='__main__':main()
