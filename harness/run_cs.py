#!/usr/bin/env python3
"""C端 Harness（25-draft 纪律）：冻结、执行、三态、复原——退出码说了算。

用法：python3 harness/run_cs.py [--quick]
产物：runs/cs/<时间戳>/<用例>/{cmd,stdout,stderr,exit,status} + summary.json
三态：pass / fail / blocked（环境缺件=诚实受阻，不冒充绿）。
复原：每轮后断言无残留 uvicorn 进程占 C 端口、临时目录清空；任何复原失败 → 整轮非零。
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUICK = '--quick' in sys.argv

# ---- 用例冻结（跑前定死；改用例=改这份清单，不接受临时口头调整）----
CASES = [
    ('unit-core',   ['python3', '-m', 'pytest', 'tests/test_cs_core.py', '-q']),
    ('unit-bot',    ['python3', '-m', 'pytest', 'tests/test_csbot.py', '-q']),
    ('unit-api',    ['python3', '-m', 'pytest', 'tests/test_cs_api.py', '-q']),
    ('regression',  ['python3', '-m', 'pytest', 'tests/', '-q', '--ignore=tests/e2e']),
    ('e2e-pages',   ['python3', '-m', 'pytest', 'tests/e2e/test_pages.py', '-q']),
    ('e2e-real',    ['python3', '-m', 'pytest', 'tests/e2e/test_real_chain.py', '-q', '-rs']),
]
if QUICK:
    CASES = [c for c in CASES if not c[0].startswith('e2e-real')]

E2E_PORT = 18890


def _cleanup_ok() -> list:
    """复原断言：返回未通过项（空=复原完整）。"""
    problems = []
    out = subprocess.run(['lsof', '-ti', f'tcp:{E2E_PORT}'], capture_output=True, text=True)
    if out.stdout.strip():
        problems.append(f'port {E2E_PORT} still held by pid {out.stdout.strip()}')
    import glob
    for d in glob.glob('/tmp/cs-e2e-*'):
        problems.append(f'tmp dir left: {d}')
    return problems


def main():
    run_dir = os.path.join(REPO, 'runs', 'cs', datetime.now().strftime('%Y%m%d-%H%M%S'))
    os.makedirs(run_dir, exist_ok=True)
    summary = []
    for name, cmd in CASES:
        cdir = os.path.join(run_dir, name)
        os.makedirs(cdir, exist_ok=True)
        open(os.path.join(cdir, 'cmd'), 'w').write(' '.join(cmd))
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1800)
        open(os.path.join(cdir, 'stdout'), 'w').write(p.stdout)
        open(os.path.join(cdir, 'stderr'), 'w').write(p.stderr)
        open(os.path.join(cdir, 'exit'), 'w').write(str(p.returncode))
        blocked = 'BLOCKED' in p.stdout or 'blocked' in p.stdout.split('reasons')[-1][:2000] \
            if name == 'e2e-real' else False
        status = 'blocked' if (blocked and p.returncode == 0) else \
            ('pass' if p.returncode == 0 else 'fail')
        summary.append({'case': name, 'status': status, 'exit': p.returncode})
        print(f"[{status:>7}] {name}  ({time.strftime('%H:%M:%S')})", flush=True)

    cleanup = _cleanup_ok()
    summary.append({'case': '_cleanup', 'status': 'pass' if not cleanup else 'fail',
                    'problems': cleanup})
    json.dump(summary, open(os.path.join(run_dir, 'summary.json'), 'w'),
              ensure_ascii=False, indent=1)
    fails = [s for s in summary if s['status'] == 'fail']
    blocks = [s for s in summary if s['status'] == 'blocked']
    print(f"\n结果：{len(summary)-1-len(blocks)} pass / {len(fails)} fail / {len(blocks)} blocked"
          f"  →  {run_dir}/summary.json", flush=True)
    for s in blocks:
        print(f"  BLOCKED: {s['case']}", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
