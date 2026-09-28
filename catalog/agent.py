"""AI Agent 解析：在隔离容器内运行 claude -p，提示词=已审批动态分类字段+实战验证的硬约束方法论。

超时拯救：到 AGENT_TIMEOUT 但结果文件已写出 → 收用（doc7 教训）。
"""
import json
import os
import shutil
import subprocess
import uuid

from . import config


def build_dynamic_prompt(template: dict, xlsx_path: str, out_json: str, sheet: str = '') -> str:
    """动态分类提示词：已审批字段注入 B端同款方法论，另补乱表规则。

    真实商家表的坑：表头位置不定、表可能极宽、商品跨行、图片行不是商品
    （如 84 列吹风机表 18 商品被代码按物理行切成 34）。
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
每个商品必须另输出 source_sheet（原 Sheet 名）和 source_rows（该商品覆盖的全部物理行号数组，含跨行规格和图片）。无法解析的区域放在 failures 数组，含 source_sheet、source_rows、reason；禁止静默遗漏。
每个商品另输出 supplier：逐行供应商优先，缺失用表内明确的厂家名，再缺失留空；不同供应商同型号是独立商品。
同一型号的不同配色/规格仍是独立商品；只有图片没有数据的行不是商品，图片按锚点归属到对应商品。
纵向合并单元格只是格式：空单元格继承上方有值单元格的内容。
内嵌图片在 xlsx（zip）的 xl/media/、锚点在 xl/drawings/：解到工作目录（r行号_c列号.扩展名），
每条商品 image_main 填主图文件名、images 填该商品全部图片文件名清单（主图排第一）、image_count 填数量。
所有字段值逐字来自单元格原文：禁止编造、改写、翻译、换算单位。
定稿前自检：商品数应等于表内逻辑商品数（不是物理行数），抽 5-10 个商品核对字段值和图片归属。

# 输出
{{"vendor":"厂家名或null","failures":[],"products":[{{"source_sheet":"Sheet1","source_rows":[5],...模板字段...,"image_main":"主图文件名","images":["该商品全部图片文件名"],"image_count":N}}]}}
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=商品数）"""


def parse_dynamic(template: dict, xlsx_path: str, work_dir: str, *, sheet: str = '') -> dict:
    """动态分类的商品解析：提示词由已审批模板字段现场生成，同一容器链路执行。"""
    prompt = build_dynamic_prompt(template, '/input/source.xlsx', '/work/products.json', sheet)
    data = _run_container(prompt, xlsx_path, work_dir)
    if not isinstance(data.get('products'), list):
        raise RuntimeError('Agent 输出必须包含 products 数组')
    # Per-item validation belongs to dynamic_import so malformed siblings do
    # not erase valid products from the same model response.
    return data


def build_template_discovery_prompt(xlsx_path: str, out_json: str, sheet: str = '') -> str:
    """模板阶段表头发现提示词：多级表头找齐、无标头图片列识别，只出结构不出商品。

    代码按行猜表头在真实商家表上翻车的两个案例：
    华岳表两级表头丢型号/图片列、琉砾表把数据行当表头——交给子代理按语义找。
    """
    sheet_clause = f"只分析工作表「{sheet}」，其他 Sheet 一律忽略。\n" if sheet else ""
    return f"""分析这份 Excel 厂家报价单的表头结构，产出每个工作表的模板字段清单（不提取商品数据）。

输入：{xlsx_path}（你的工作目录）
输出：{out_json}
{sheet_clause}# 硬性规则
表头不一定在第一行：可能在标题行下方，也可能是多级表头（跨行/跨列合并单元格——合并值只在左上角，语义覆盖整个合并区域）。逐列把每一列的表头找齐：多级表头把各级语义拼成一个 label（如上级「ITEM.NO」+下级「型号」→「ITEM.NO 型号」）。
没有表头文字、但该列锚定了内嵌图片的列=图片列：label 固定「图片」、role=image、type=image。内嵌图片在 xlsx（zip）的 xl/media/，锚点在 xl/drawings/（按 drawing 锚点的列号判断图片落在哪一列）。
既没有表头文字、该列又没有图片的空列直接跳过；禁止发明表里不存在的列。
一张 Sheet 里可能有多个区块/多张小表：只取主商品表（商品行最多的那张），其余区块忽略。
header_row=主商品表表头区块的起始行号（多级表头取最上一级所在行）。
label 用表头单元格原文拼接，禁止翻译/改写/换算；role 只能取白名单：
model=该表唯一的型号/货号/品名列（没有就给 spec）；image=图片列；sequence=纯序号列；
price=价格/报价/单价类；cost=成本/进货价类；stock=库存；note=备注/说明/链接类；其余一律 spec。
type：image 列给 image，价格/成本给 money，序号/库存给 number，其余 text。
visibility：价格、成本、库存、供应商类内部信息一律 internal，其余 public。
searchable：只对客户会拿来搜索的字段（型号、品名）给 true，其余 false。

# 输出
{{"sheets":[{{"title":"Sheet 名","header_row":N,"columns":[{{"col":1,"label":"表头原文","role":"model","type":"text","visibility":"public","searchable":true}},{{"col":2,"label":"图片","role":"image","type":"image","visibility":"public","searchable":false}}]}}]}}
只输出有主商品表的 Sheet；col 从 1 开始、按列号升序。
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=Sheet 数）"""


def parse_dynamic_template(xlsx_path: str, work_dir: str, sheet: str = '') -> dict:
    """模板阶段表头发现：同一容器链路，让子代理找齐多级表头与无标头图片列。

    返回 {"sheets":[{"title","header_row","columns":[{col,label,role,type,visibility,searchable}]}]}；
    结构无效（缺 sheets/非对象数组）抛 RuntimeError，由调用方回落代码发现。
    """
    prompt = build_template_discovery_prompt('/input/source.xlsx', '/work/template.json', sheet)
    data = _run_container(prompt, xlsx_path, work_dir, out_name='template.json')
    sheets = data.get('sheets')
    if not isinstance(sheets, list) or not all(isinstance(s, dict) for s in sheets):
        raise RuntimeError('Agent 模板输出必须包含 sheets 对象数组')
    return data


def _run_container(prompt: str, xlsx_path: str, work_dir: str, *, out_name: str = 'products.json') -> dict:
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
    out_json = os.path.join(work_dir, out_name)
    if os.path.exists(out_json):
        os.remove(out_json)
    # Only the parser-specific API credential enters the container. No messaging,
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
    network = os.environ.get('CATALOG_AGENT_NETWORK', '')
    if network:
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',network) or network in ('host','none','bridge'):
            raise ValueError('解析网络必须是明确的专用 Docker 网络')
        command[2:2] = ['--network',network]
    timed_out = False
    try:
        process = subprocess.run(
            command,
            capture_output=True, text=True, timeout=config.AGENT_TIMEOUT,
            env=env, cwd=work_dir)
    except subprocess.TimeoutExpired:
        timed_out = True   # 超时拯救：产物已写出则收用
        subprocess.run([docker, 'rm', '--force', container_name], capture_output=True, timeout=15, env=env)
    if not timed_out and process.returncode != 0:
        raise RuntimeError('Agent 解析进程失败，请重试')
    if not os.path.exists(out_json):
        raise RuntimeError(f'Agent 未产出结果文件(timed_out={timed_out})')
    if timed_out:
        print('[agent] 超时拯救：收用已写出的结果文件', flush=True)
    if os.path.islink(out_json) or os.path.getsize(out_json) > 20 * 1024 * 1024:
        raise RuntimeError('Agent 结果文件无效或过大')
    with open(out_json, encoding='utf-8') as result:
        data = json.load(result)
    if not isinstance(data, dict):
        raise RuntimeError('Agent 结果必须是 JSON 对象')
    if timed_out:
        data.setdefault('failures', []).append({'source_sheet': '', 'source_rows': [], 'reason': '解析超时，仅保留已验证结果；其余区域覆盖未确认'})
    return data
