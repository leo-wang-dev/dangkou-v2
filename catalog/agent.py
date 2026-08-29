"""AI Agent 解析：spawn claude -p，提示词=品类模板字段+实战验证的硬约束方法论。

超时拯救：到 AGENT_TIMEOUT 但结果文件已写出 → 收用（doc7 教训）。
"""
import json
import os
import shutil
import subprocess

from . import config
from .templates import TEMPLATES

PROMPT = '''解析这份 Excel 厂家报价单，按品类模板产出商品库。

输入：__FILE__（你的工作目录）
输出：__OUT__
品类：__CAT_NAME__，模板字段（键名=列名，逐字段填，原文有就填没有留空）：__FIELDS__

# 硬性验收标准
同产品型号绝不能出现两条——同型号多行合并为一条，颜色/规格变体差异值用 / 连接；
纵向合并单元格的从属行必须继承组首行值归入同一商品。
内嵌图片在 xlsx（zip）的 xl/media/、锚点在 xl/drawings/：解到工作目录（r行号_c列号.扩展名），
每条商品 image_main 填主图文件名、images 填该商品全部图片文件名清单（主图排第一）、image_count 填数量。
定稿前自检：无重复型号、商品数合理（通常约等于数据行数），并抽 5-10 行核对归属。

# 输出
{"vendor":"厂家名或null","products":[{...模板字段...,"image_main":"主图文件名","images":["该商品全部图片文件名按主图在前排列"],"image_count":N}]}
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=商品数）'''


def build_prompt(template_key, xlsx_path, out_json) -> str:
    t = TEMPLATES[template_key]
    fields = ', '.join(f'{label}({col})' for col, label in t.fields)
    return (PROMPT.replace('__FILE__', xlsx_path)
            .replace('__OUT__', out_json)
            .replace('__CAT_NAME__', t.name)
            .replace('__FIELDS__', fields))


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
    # 防御性去重（同型号保留字段更全的）
    t = TEMPLATES[template_key]
    best = {}
    for p in data.get('products', []):
        k = str(p.get(t.dedup_field, '')).strip()
        if not k:
            continue
        cur = json.dumps(p, ensure_ascii=False)
        if k not in best or len(cur) > len(json.dumps(best[k], ensure_ascii=False)):
            best[k] = p
    data['products'] = list(best.values())
    return data
