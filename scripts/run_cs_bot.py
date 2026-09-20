"""Durable Telegram inbox: save a whole batch before acknowledging its offset."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from catalog import config, db
from catalog.csbot import CsBot
from catalog.tg import TgApi


def process_pending(conn, bot, limit=1000):
    """Process oldest due message per customer, preserving order without loading the backlog."""
    processed = 0
    while processed < limit:
        rows = conn.execute("""
            SELECT * FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY COALESCE(json_extract(payload,'$.message.chat.id'),update_id)
                ORDER BY update_id) AS position
              FROM cs_inbox WHERE processed=0
            ) WHERE position=1 AND next_attempt_at<=datetime('now')
            ORDER BY update_id LIMIT ?
        """, (min(100,limit-processed),)).fetchall()
        if not rows:
            break
        for row in rows:
            try:
                bot.handle_update(json.loads(row['payload']))
                # handle_update commits processed=1; stubs should obey the same contract.
            except Exception as exc:
                conn.rollback()
                delay=min(900,30 * 2 ** min(row['attempts'],5))
                conn.execute("UPDATE cs_inbox SET attempts=attempts+1,last_error=?,next_attempt_at=datetime('now',?) WHERE update_id=?",
                             (type(exc).__name__,f'+{delay} seconds',row['update_id']))
                conn.commit()
                print(f'[cs-bot] update {row["update_id"]} 失败，已保留并延迟重试：{type(exc).__name__}',flush=True)
            processed += 1
    return processed


def main():
    conn = db.connect()
    db.init_db(conn)
    api = TgApi()
    from catalog.shop_link import verify_bot
    verify_bot(conn, api._call('getMe'))
    if os.environ.get('CATALOG_CS_API_URL'):
        from catalog.customer_catalog import products
        products(conn)  # Refuse a different shop or an unavailable merchant API.
    bot = CsBot(conn, api)
    last = conn.execute('SELECT MAX(update_id) FROM cs_inbox').fetchone()[0]
    if last is not None:
        api._offset = last + 1
    if os.environ.get('MERCHANT_READY_FILE'):
        from pathlib import Path
        Path(os.environ['MERCHANT_READY_FILE']).write_text(str(os.getpid()))
    print('[cs-bot] 启动持久化收发队列', flush=True)
    while True:
        try:
            bot.flush_outbox()
            process_pending(conn, bot)
            updates = api.poll()
            for upd in updates:
                conn.execute('INSERT OR IGNORE INTO cs_inbox(update_id,payload) VALUES(?,?)',
                             (upd['update_id'], json.dumps(upd, ensure_ascii=False)))
            conn.commit()
            if updates:
                api._offset = max(upd['update_id'] for upd in updates) + 1
            process_pending(conn, bot)
            bot.flush_outbox()
        except KeyboardInterrupt:
            print('[cs-bot] 收到退出信号', flush=True)
            return
        except Exception as exc:
            conn.rollback()
            print(f'[cs-bot] 轮询异常（10s后继续）: {type(exc).__name__}', flush=True)
            time.sleep(10)


if __name__ == '__main__':
    import fcntl
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    with open(config.DB_PATH + '.bot.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
