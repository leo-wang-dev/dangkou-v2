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


def build_dynamic_prompt(template: dict, xlsx_path: str, out_json: str, sheet: str = '') -> str:
    """动态分类提示词：已审批字段注入 B端同款方法论，另补乱表规则。

    与固定品类的差别：表头位置不定、表可能极宽、商品跨行、图片行不是商品——
    这些是动态商家真实表（如 84 列吹风机表 18 商品被代码按物理行切成 34）的实测坑。
    """
    lines = []
    for f in template['fields']:
        if f.get('role') == 'image':
            continue
        hint = ''
        if f.get('role') == 'model':
            hint = ' ← 型号字段，尽量必填'
        elif f.get('role') == 'note':
            hint = ' ← 模板字段装不下的补充信息拼这里，没有留空'
        lines.append(f"  {f['label']}({f['key']}){hint}")
    sheet_clause = f"只处理工作表「{sheet}」，其他 Sheet 一律忽略。\n" if sheet else ""
    field_block = '\n'.join(lines)
    return f"""解析这份 Excel 厂家报价单，按品类模板产出商品库。

输入：{xlsx_path}（你的工作目录）
输出：{out_json}
品类：{template['name']}
{sheet_clause}模板字段（括号内=输出键名，逐字段填，原文有就填没有留空）：
{field_block}

# 硬性验收标准
表头不一定在第一行（可能在中部、可能是多级表头）：先定位真实表头再取数；表格可能很宽（几十列）。
一个逻辑商品可能占多个物理行（数据行+规格行+图片行）：按型号/品名判断归属，合并为一个商品；
同一型号的不同配色/规格仍是独立商品；只有图片没有数据的行不是商品，图片按锚点归属到对应商品。
纵向合并单元格只是格式：空单元格继承上方有值单元格的内容。
内嵌图片在 xlsx（zip）的 xl/media/、锚点在 xl/drawings/：解到工作目录（r行号_c列号.扩展名），
每条商品 image_main 填主图文件名、images 填该商品全部图片文件名清单（主图排第一）、image_count 填数量。
所有字段值逐字来自单元格原文：禁止编造、改写、翻译、换算单位。
定稿前自检：商品数应等于表内逻辑商品数（不是物理行数），抽 5-10 个商品核对字段值和图片归属。

# 输出
{{"vendor":"厂家名或null","products":[{{...模板字段...,"image_main":"主图文件名","images":["该商品全部图片文件名"],"image_count":N}}]}}
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=商品数）"""


def parse(template_key, xlsx_path, work_dir) -> dict:
    prompt = build_prompt(template_key, '/input/source.xlsx', '/work/products.json')
    return _run_container(prompt, xlsx_path, work_dir)


def parse_dynamic(template: dict, xlsx_path: str, work_dir: str, *, sheet: str = '') -> dict:
    """动态分类的商品解析：提示词由已审批模板字段现场生成，同一容器链路执行。"""
    prompt = build_dynamic_prompt(template, '/input/source.xlsx', '/work/products.json', sheet)
    return _run_container(prompt, xlsx_path, work_dir)


def _run_container(prompt: str, xlsx_path: str, work_dir: str) -> dict:
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
