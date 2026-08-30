"""品类模板常量——本系统字段体系的唯一事实源（PRD §3 人工确认后固化）。"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    table: str
    dedup_field: str
    fields: tuple  # ((col, label), ...) 有序=列序
    notes: str = ''  # 品类专属提示词补充（用户按实际微调）


RAZOR = Template('razor', '剃须刀', 'product_razor', 'model_no', (
    ('model_no', '产品型号'), ('description', '功能描述'), ('color', '颜色'),
    ('size_mm', '产品尺寸(mm)'), ('giftbox_mm', '彩盒尺寸(mm)'),
    ('unit_weight_g', '单套重量(g)'), ('ctn_spec', '箱规'), ('price', '报价'),
))
CURLER = Template('curler', '卷发棒', 'product_curler', 'item_no', (
    ('item_no', 'ITEM.NO 型号'), ('ctn_size', '装箱尺寸'), ('ctn_qty', '装箱数量'),
    ('price', '价格'), ('voltage', '电压'), ('power', '功率'),
    ('heater', '发热体'), ('material', '材质'), ('frequency', '频率'),
))
TEMPLATES = {t.key: t for t in (RAZOR, CURLER)}


def row_to_dict(t: Template, row) -> dict:
    keys = row.keys() if hasattr(row, 'keys') else ()
    d = {label: row[col] for col, label in t.fields if col in keys}
    import json as _json
    imgs = row['images'] if 'images' in keys else '[]'
    d.update({'内部货号': row['inner_code'], '状态': row['status'],
              '主图': row['image_main'], 'id': row['id'],
              '图集': _json.loads(imgs or '[]')})
    return d
