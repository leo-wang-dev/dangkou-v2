"""AI Agent 解析：在隔离容器内运行 claude -p，提示词=品类模板字段+实战验证的硬约束方法论。

超时拯救：到 AGENT_TIMEOUT 但结果文件已写出 → 收用（doc7 教训）。
"""
import json
import os
import shutil
import subprocess
import uuid

from . import config
from .templates import TEMPLATES

def build_prompt(template_key, xlsx_path, out_json) -> str:
    t = TEMPLATES[template_key]
    return (t.prompt
            .replace('__FILE__', xlsx_path)
            .replace('__OUT__', out_json))


def parse(template_key, xlsx_path, work_dir) -> dict:
    docker = shutil.which('docker')
    image = os.environ.get('CATALOG_AGENT_CONTAINER_IMAGE', '')
    if not docker or not image:
        raise RuntimeError('Excel 解析需要 Docker 和 CATALOG_AGENT_CONTAINER_IMAGE；禁止在服务用户下直接执行不可信文件解析')
    if image.startswith('-') or any(ch.isspace() for ch in image):
        raise ValueError('解析镜像名称无效')
    source = os.path.realpath(xlsx_path)
    work_dir = os.path.realpath(work_dir)
    if not os.path.isfile(source) or any(',' in p for p in (source, work_dir)):
        raise ValueError('解析输入不存在或路径含不支持的逗号')
    os.makedirs(work_dir, exist_ok=True)
    out_json = os.path.join(work_dir, 'products.json')
    if os.path.exists(out_json):
        os.remove(out_json)
    prompt = build_prompt(template_key, '/input/source.xlsx', '/work/products.json')
    # Only the parser-specific API credential enters the container. No Telegram,
    # merchant notification or catalog service token is inherited.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT') if k in os.environ}
    env['ANTHROPIC_BASE_URL'] = config.AGENT_BASE_URL
    env['ANTHROPIC_API_KEY'] = config.AGENT_API_KEY
    container_name = 'dangkou-parser-' + uuid.uuid4().hex
    command = [docker, 'run', '--name', container_name, '--init', '--rm', '--read-only', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=1g', '--cpus=1',
               '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
               '--env', 'HOME=/tmp', '--env', 'ANTHROPIC_BASE_URL', '--env', 'ANTHROPIC_API_KEY',
               '--mount', f'type=bind,source={source},target=/input/source.xlsx,readonly',
               '--mount', f'type=bind,source={work_dir},target=/work', '--workdir', '/work',
               '--entrypoint', 'claude', image,
               '-p', prompt, '--output-format', 'json', '--dangerously-skip-permissions',
               '--model', config.AGENT_MODEL]
    timed_out = False
    try:
        subprocess.run(
            command,
            capture_output=True, text=True, timeout=config.AGENT_TIMEOUT,
            env=env, cwd=work_dir)
    except subprocess.TimeoutExpired:
        timed_out = True   # 超时拯救：产物已写出则收用
        subprocess.run([docker, 'rm', '--force', container_name], capture_output=True, timeout=15, env=env)
    if not os.path.exists(out_json):
        raise RuntimeError(f'Agent 未产出结果文件(timed_out={timed_out})')
    if timed_out:
        print('[agent] 超时拯救：收用已写出的结果文件', flush=True)
    if os.path.islink(out_json) or os.path.getsize(out_json) > 20 * 1024 * 1024:
        raise RuntimeError('Agent 结果文件无效或过大')
    with open(out_json, encoding='utf-8') as result:
        data = json.load(result)
    if not isinstance(data, dict) or not isinstance(data.get('products'), list) or not all(isinstance(p, dict) for p in data['products']):
        raise RuntimeError('Agent 输出必须包含 products 对象数组')
    return data
