"""C端客服域服务：阶梯价（结构化）+ 红线知识（自然语言注入）。

v1.2 两个判定：
① 阶梯价=结构化档位表（`20:12;50:11`），取档由代码计算，AI 不做算术；
② 红线=注入 prompt 的自然语言知识——存商家原文+总结版，店级+商品级（商品覆盖店级）。
"""
import re

DEFAULT_STORE_REDLINE = (
    '数量低于20个的询价转人工；客户要求价格低于出厂价1.2倍的转人工；'
    '询底价、账期、质量投诉、能否定制的转人工。')

SYSTEM_HARD_RULE = '军火弹药及违禁品询价：无条件转人工，永不自动回复（系统级，商家不可关闭）。'


# ---------- 判定①：阶梯价 ----------

def parse_tiers(text) -> list:
    """'20:12;50:11;100:10.5' → [(20,12.0),...] 升序；口语变体容忍；解析不了空表。"""
    if not text:
        return []
    out = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*[::：个]\s*(\d+(?:\.\d+)?)', str(text)):
        qty, price = float(m.group(1)), float(m.group(2))
        if qty > 0 and price > 0 and (int(qty), price) not in [(int(q), p) for q, p in out]:
            out.append((int(qty), price))
    return sorted(out)


def pick_tier(tiers, qty):
    """数量→档位单价（取满足数量的最高档）；低于最低档返回 None（转人工）。"""
    for threshold, price in reversed(tiers):
        if qty >= threshold:
            return price
    return None


# ---------- 判定②：红线知识 ----------

def get_redline(conn, product_id=None) -> dict:
    """商品级优先，未设返回店级；连店级都没有（理论不会，init 播种）回默认。"""
    if product_id:
        row = conn.execute('SELECT * FROM cs_redline WHERE product_id=?', (product_id,)).fetchone()
        if row:
            return _row2dict(row)
    row = conn.execute("SELECT * FROM cs_redline WHERE product_id=''").fetchone()
    if row:
        return _row2dict(row)
    return {'product_id': None, 'text_raw': DEFAULT_STORE_REDLINE,
            'text_summary': DEFAULT_STORE_REDLINE}


def set_redline(conn, product_id, text_raw, text_summary=None):
    """覆盖式写入（UNIQUE(product_id)，''=店级）。text_summary 缺省=原文。"""
    text_summary = text_summary or text_raw
    conn.execute(
        "INSERT INTO cs_redline(product_id, text_raw, text_summary) VALUES(?,?,?) "
        'ON CONFLICT(product_id) DO UPDATE SET '
        'text_raw=excluded.text_raw, text_summary=excluded.text_summary, '
        "updated_at=datetime('now')",
        (product_id or '', text_raw, text_summary))
    conn.commit()
    return get_redline(conn, product_id)


def summarize(text, llm=False):
    """注入版：短文直录；超长（约>500字）走 LLM 压缩。"""
    if not llm or len(text) <= 500:
        return text
    return _llm_summarize(text)


def _llm_summarize(text):  # 测试可替换；生产接 llm.chat_text
    from . import llm
    return llm.compress(text)


def build_knowledge(conn, product_ids=None) -> str:
    """组装注入客服 prompt 的红线知识块：系统级 + 店级 + 涉及商品的商品级。"""
    parts = [f'【系统级（不可改）】{SYSTEM_HARD_RULE}']
    red = get_redline(conn)
    parts.append(f'【全店红线（商家设定）】{red["text_summary"]}')
    for pid in (product_ids or []):
        prow = conn.execute(
            'SELECT text_summary FROM cs_redline WHERE product_id=?', (pid,)).fetchone()
        if prow:
            parts.append(f'【本商品红线（商家设定）】{prow["text_summary"]}')
    return '\n'.join(parts)


def _row2dict(row):
    return {'product_id': row['product_id'] or None, 'text_raw': row['text_raw'],
            'text_summary': row['text_summary'], 'updated_at': row['updated_at']}
