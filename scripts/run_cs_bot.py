"""C端 TG 客服常驻进程：长轮询收消息 → CsBot 处理 → 回复。

用法：python3 scripts/run_cs_bot.py
依赖 .env 的 TG_BOT_TOKEN；与 sidecar（:8890）同机同库跑。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from catalog import config, db  # noqa: E402
from catalog.csbot import CsBot  # noqa: E402
from catalog.tg import TgApi  # noqa: E402


def main():
    conn = db.connect()
    db.init_db(conn)
    api = TgApi()                       # 读 TG_BOT_TOKEN（S1 定案：长轮询+重试）
    bot = CsBot(conn, api)
    print(f'[cs-bot] 启动：bot token 已加载，开始长轮询', flush=True)
    while True:
        try:
            for upd in api.poll():
                t0 = time.time()
                bot.handle_update(upd)
                print(f'[cs-bot] update {upd.get("update_id")} 处理 {time.time()-t0:.1f}s',
                      flush=True)
        except KeyboardInterrupt:
            print('[cs-bot] 收到退出信号', flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f'[cs-bot] 轮询异常（10s后继续）: {type(e).__name__} {e}', flush=True)
            time.sleep(10)


if __name__ == '__main__':
    main()
