"""品类模板常量——每个品类独立完整的提示词（用户按实际微调各自调）。"""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    table: str
    dedup_field: str
    fields: tuple  # ((col, label), ...) 有序=列序
    prompt: str = ''  # 该品类完整独立提示词


# ============ 剃须刀提示词（独立，微调只改这里） ============
RAZOR_PROMPT = '''解析这份 Excel 厂家报价单，按品类模板产出商品库。

输入：__FILE__（你的工作目录）
输出：__OUT__
品类：剃须刀
模板字段（键名=列名，逐字段填，原文有就填没有留空）：
  产品型号(model_no), 功能描述(description), 颜色(color),
  产品尺寸mm(size_mm), 彩盒尺寸mm(giftbox_mm), 单套重量g(unit_weight_g),
  箱规(ctn_spec), 报价(price)

# 硬性验收标准
每一行数据 = 一个独立商品，不做任何合并。
纵向合并单元格只是格式：空单元格继承上方有值单元格的内容（如品名只在组首行写），但每一行仍然是一个独立商品。
内嵌图片在 xlsx（zip）的 xl/media/、锚点在 xl/drawings/：解到工作目录（r行号_c列号.扩展名），
每条商品 image_main 填主图文件名、images 填该商品全部图片文件名清单（主图排第一）、image_count 填数量。
定稿前自检：商品数应等于数据行数，并抽 5-10 行核对归属。

# 输出
{"vendor":"厂家名或null","products":[{...模板字段...,"image_main":"主图文件名","images":["该商品全部图片文件名"],"image_count":N}]}
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=商品数）'''


# ============ 卷发棒提示词（独立，微调只改这里） ============
CURLER_PROMPT = '''解析这份 Excel 厂家报价单，按品类模板产出商品库。

输入：__FILE__（你的工作目录）
输出：__OUT__
品类：卷发棒
模板字段（键名=列名，逐字段填，原文有就填没有留空）：
  ITEM.NO型号(item_no), 装箱尺寸(ctn_size), 装箱数量(ctn_qty),
  价格(price), 电压(voltage), 功率(power),
  发热体(heater), 材质(material), 频率(frequency)

# 硬性验收标准
每一行数据 = 一个独立商品，不做任何合并。
纵向合并单元格只是格式：空单元格继承上方有值单元格的内容，但每一行仍然是一个独立商品。
内嵌图片在 xlsx（zip）的 xl/media/、锚点在 xl/drawings/：解到工作目录（r行号_c列号.扩展名），
每条商品 image_main 填主图文件名、images 填该商品全部图片文件名清单（主图排第一）、image_count 填数量。
定稿前自检：商品数应等于数据行数，并抽 5-10 行核对归属。

# 输出
{"vendor":"厂家名或null","products":[{...模板字段...,"image_main":"主图文件名","images":["该商品全部图片文件名"],"image_count":N}]}
用 python 的 json.dump(..., ensure_ascii=False, indent=1) 写入输出文件。
完成后只回一行：DONE N（N=商品数）'''


RAZOR = Template('razor', '剃须刀', 'product_razor', 'model_no', (
    ('model_no', '产品型号'), ('description', '功能描述'), ('color', '颜色'),
    ('size_mm', '产品尺寸(mm)'), ('giftbox_mm', '彩盒尺寸(mm)'),
    ('unit_weight_g', '单套重量(g)'), ('ctn_spec', '箱规'), ('price', '报价'),
), prompt=RAZOR_PROMPT)

CURLER = Template('curler', '卷发棒', 'product_curler', 'item_no', (
    ('item_no', 'ITEM.NO 型号'), ('ctn_size', '装箱尺寸'), ('ctn_qty', '装箱数量'),
    ('price', '价格'), ('voltage', '电压'), ('power', '功率'),
    ('heater', '发热体'), ('material', '材质'), ('frequency', '频率'),
), prompt=CURLER_PROMPT)

TEMPLATES = {t.key: t for t in (RAZOR, CURLER)}


def row_to_dict(t: Template, row) -> dict:
    keys = row.keys() if hasattr(row, 'keys') else ()
    d = {label: row[col] for col, label in t.fields if col in keys}
    imgs = row['images'] if 'images' in keys else '[]'
    d.update({'内部货号': row['inner_code'], '状态': row['status'],
              '主图': row['image_main'], 'id': row['id'],
              '图集': json.loads(imgs or '[]')})
    return d
