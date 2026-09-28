"""独立通知进程：cs_outbox 的唯一出站投递循环（删D 通知合一）。

消费全部剩余渠道（notify/notify_file/notify_import），经 catalog-notify
引擎桥 HTTP 直发微信（catalog/notify.wechat_remind / wechat_file，调用保持
现状）。CsBot/H5 路由只写队列（_enqueue），不负责投递；没有分区消费者开关
（CATALOG_NOTIFY_WORKER 已删除）。进程带文件锁，同库只跑一个实例。
"""
import fcntl
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import config, db, notify, ingest


def run(conn, rounds=None):
    """投递主循环；rounds=None 无限轮询（生产），测试可限定轮数。"""
    n = 0
    while rounds is None or n < rounds:
        ingest.recover(conn)
        notify.deliver(conn)
        n += 1
        if rounds is None or n < rounds:
            time.sleep(2)


def main():
    conn = db.connect()
    db.init_db(conn)
    try:
        run(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    Path(config.DB_PATH).resolve().parent.mkdir(parents=True, exist_ok=True)
    with open(config.DB_PATH + '.notify.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            main()
        except KeyboardInterrupt:
            pass
