"""Standalone merchant outbox worker: the single delivery loop for cs_outbox.

C 端 TG 传输拆除（删C）后，出站只剩微信通知渠道；本进程不再需要任何
TG Token 或轮询依赖，也没有分区消费者开关。
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
        bot=CsBot(conn,api=None)
        while True:
            bot.flush_outbox()
            time.sleep(2)
    finally:
        conn.close()


if __name__=='__main__':
    Path(config.DB_PATH).resolve().parent.mkdir(parents=True,exist_ok=True)
    with open(config.DB_PATH+'.notify.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:main()
        except KeyboardInterrupt:pass
