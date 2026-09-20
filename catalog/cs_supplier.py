"""Explicit purchase-source metadata, separate from the bot owner's shop profile."""
import json
import re

FIELDS = ('档口名称', '档口号/地址', '供应商联系人', '供应商联系方式')
MISSING = '待补充'


def normalize(fields):
    return {**{k: fields.get(k) or MISSING for k in FIELDS},
            **{k: v for k, v in fields.items() if k not in FIELDS}}


def assign(conn, customer_id, text):
    """Explicit list ordinals only: never infer a supplier from the product brand."""
    match = re.fullmatch(r'清单第\s*([0-9、,，\s]+)\s*条\s*(档口|档口名称|档口号/地址|供应商联系人|供应商联系方式)\s*[:：]\s*(.+)', text.strip())
    if not match:
        return None
    numbers = sorted(set(int(n) for n in re.findall(r'\d+', match[1])))
    notes = conn.execute("SELECT * FROM cs_note WHERE customer_id=? AND status IN ('draft','confirmed') ORDER BY id", (customer_id,)).fetchall()
    if not numbers or any(n < 1 or n > len(notes) for n in numbers):
        return f'清单目前有 {len(notes)} 条，请使用网页或 Excel 中的序号指定条目。未修改任何记录。'
    key = '档口名称' if match[2] == '档口' else match[2]
    value = match[3].strip()
    if len(value) > 300:
        return '档口信息过长，请控制在300字以内。未修改任何记录。'
    from . import shop_link
    if key=='档口名称' and value in ('本店','当前档口'):
        p=shop_link.profile(conn)
        if not p['shop_name'] or not p['tg_bot_id']:
            return '当前档口尚未完成 bot 绑定。未修改任何记录。'
    for number in numbers:
        shop_link.set_field(conn,notes[number-1],key,value)
    return f'已更新清单第{"、".join(map(str,numbers))}条：{key}={value}。确认状态不变；重新出表即可下载更新后的 Excel。'
