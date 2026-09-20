"""Standalone merchant outbox worker; no Telegram token or polling dependency.

Run with CATALOG_NOTIFY_WORKER=1 also set for run_cs_bot.py to partition consumers.
"""
import fcntl
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config, db
from catalog.csbot import CsBot


def main():
    conn=db.connect();db.init_db(conn)
    try:
        # Only the notification branch is used; no TgApi initialization is needed.
        bot=CsBot(conn,api=None)
        while True:
            bot.flush_outbox(notifications_only=True)
            time.sleep(2)
    finally:
        conn.close()


if __name__=='__main__':
    Path(config.DB_PATH).resolve().parent.mkdir(parents=True,exist_ok=True)
    with open(config.DB_PATH+'.notify.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:main()
        except KeyboardInterrupt:pass
