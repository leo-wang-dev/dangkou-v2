"""AI Agent 解析：spawn claude -p，提示词=品类模板字段+实战验证的硬约束方法论。

超时拯救：到 AGENT_TIMEOUT 但结果文件已写出 → 收用（doc7 教训）。
"""
import json
import os
import shutil
import subprocess

from . import config
from .templates import TEMPLATES

def build_prompt(template_key, xlsx_path, out_json) -> str:
    t = TEMPLATES[template_key]
    return (t.prompt
            .replace('__FILE__', xlsx_path)
            .replace('__OUT__', out_json))


def parse(template_key, xlsx_path, work_dir) -> dict:
    claude = shutil.which('claude')
    if not claude:
        raise RuntimeError('claude CLI 不在 PATH')
    out_json = os.path.join(work_dir, 'products.json')
    if os.path.exists(out_json):
        os.remove(out_json)
    prompt = build_prompt(template_key, xlsx_path, out_json)
    env = os.environ.copy()
    env['ANTHROPIC_BASE_URL'] = config.AGENT_BASE_URL
    env['ANTHROPIC_API_KEY'] = config.AGENT_API_KEY
    timed_out = False
    try:
        subprocess.run(
            [claude, '-p', prompt, '--output-format', 'json',
             '--dangerously-skip-permissions', '--model', config.AGENT_MODEL],
            capture_output=True, text=True, timeout=config.AGENT_TIMEOUT,
            env=env, cwd=work_dir)
    except subprocess.TimeoutExpired:
        timed_out = True   # 超时拯救：产物已写出则收用
    if not os.path.exists(out_json):
        raise RuntimeError(f'Agent 未产出结果文件(timed_out={timed_out})')
    if timed_out:
        print('[agent] 超时拯救：收用已写出的结果文件', flush=True)
    data = json.load(open(out_json))
    return data
