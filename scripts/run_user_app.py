"""平台中央用户工具服务启动器（新G）：独立进程/端口/库，不挂进档口 api。

朴素风格与 run_notifications 一致：读 env（USER_APP_PORT 默认 19100）、
build_app 内建库表（init 风格 CREATE IF NOT EXISTS）、直接跑。库路径由
USER_APP_DB 控制（默认 data/userapp.db），验证码桩日志 USER_APP_CODES_LOG。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import uvicorn
    from catalog import userapp
    uvicorn.run(userapp.build_app(), host='127.0.0.1',
                port=int(os.environ.get('USER_APP_PORT', '19100')), access_log=False)


if __name__ == '__main__':
    main()
