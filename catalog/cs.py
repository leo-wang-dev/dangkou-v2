"""C端客服域服务：仅使用商家已提交并审批的自然语言规则。"""
import re

DEFAULT_STORE_REDLINE = ''
SYSTEM_HARD_RULE = ''


def reject_tiers(changes):
    if any(k in changes for k in ('tier_price','阶梯价','阶梯报价')):
        raise ValueError('阶梯报价已取消，不支持录入；客户是否转人工只按商家已审批红线判断')


def normalize_visible(value):
    text = str(value).strip().lower()
    if text in ('1', 'true'):
        return '1'
    if text in ('0', 'false'):
        return '0'
    raise ValueError('可观测只能是 1 或 0')


def validate_product(conn, template, pid, changes):
    """Validate the merged state, both when creating a ticket and applying it."""
    reject_tiers(changes)
    current = conn.execute(f'SELECT * FROM {template.table} WHERE id=?', (pid,)).fetchone() if pid else None
    if pid and current is None:
        raise ValueError('商品不存在')
    visible = normalize_visible(changes.get('cs_visible', current['cs_visible'] if current else 0))
    if 'cs_visible' in changes:
        changes['cs_visible'] = visible


# ---------- 判定②：红线知识 ----------

def wechat_managed(conn):
    from . import merchant_policy
    return bool((merchant_policy.read(conn) or {}).get('wechat_managed'))


def get_redline(conn, product_id=None) -> dict:
    """商品级优先，未设返回店级；未设置则返回空规则。"""
    if product_id:
        row = conn.execute('SELECT * FROM cs_redline WHERE product_id=?', (product_id,)).fetchone()
        if row and row['text_raw'].strip():
            return _row2dict(row)
    row = conn.execute("SELECT * FROM cs_redline WHERE product_id=''").fetchone()
    if row:
        return _row2dict(row)
    return {'product_id': None, 'text_raw': '', 'text_summary': ''}


def set_redline(conn, product_id, text_raw, text_summary=None, commit=True):
    """覆盖式写入（UNIQUE(product_id)，''=店级）。text_summary 缺省=原文。"""
    text_summary = text_summary or text_raw
    conn.execute(
        "INSERT INTO cs_redline(product_id, text_raw, text_summary) VALUES(?,?,?) "
        'ON CONFLICT(product_id) DO UPDATE SET '
        'text_raw=excluded.text_raw, text_summary=excluded.text_summary, '
        "updated_at=datetime('now')",
        (product_id or '', text_raw, text_summary))
    if commit:
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
    parts = []
    red = get_redline(conn)
    if red['text_summary']:
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


def get_shop(conn):
    row = conn.execute('SELECT shop_id,shop_name,stall_no,contact_name,tg_bot_id,tg_bot_username,owner_tg_username,owner_wechat,address,business_hours,shipping_info,faq FROM shop_profile WHERE id=1').fetchone()
    return dict(row) if row else {'owner_tg_username': '', 'owner_wechat': ''}


def validate_shop(changes):
    if not changes or set(changes) - {'shop_name', 'stall_no', 'contact_name', 'tg_bot_id', 'tg_bot_username', 'owner_tg_username', 'owner_wechat', 'address', 'business_hours', 'shipping_info', 'faq'}:
        raise ValueError('仅支持档口资料、TG bot 绑定、老板联系方式及店铺说明')
    changes = {k: str(v).strip() for k, v in changes.items()}
    if 'tg_bot_id' in changes and not re.fullmatch(r'[1-9][0-9]{0,19}',changes['tg_bot_id']):
        raise ValueError('TG bot ID 必须为正整数，不是老板账号或 Token')
    if 'tg_bot_username' in changes:
        username = changes['tg_bot_username'].removeprefix('https://t.me/').lstrip('@')
        if username and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}',username):
            raise ValueError('bot Username 格式无效')
        changes['tg_bot_username'] = username
    if 'shop_name' in changes and not changes['shop_name']:
        raise ValueError('档口名称不能为空')
    if 'owner_tg_username' in changes:
        username = changes['owner_tg_username'].removeprefix('https://t.me/').lstrip('@')
        if username and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username):
            raise ValueError('TG Username 格式无效')
        changes['owner_tg_username'] = username
    if len(changes.get('owner_wechat', '')) > 100 or any(c in changes.get('owner_wechat', '') for c in '\r\n'):
        raise ValueError('微信号格式无效')
    if any(len(v)>4000 for v in changes.values()):
        raise ValueError('店铺资料单项不能超过4000字')
    return changes


def contact_reply(conn):
    profile = get_shop(conn)
    lines = ['这个问题请直接联系老板：']
    if profile['owner_tg_username']:
        lines.append(f"Telegram：@{profile['owner_tg_username']}（https://t.me/{profile['owner_tg_username']}）")
    if profile['owner_wechat']:
        lines.append(f"微信：{profile['owner_wechat']}")
    if len(lines) == 1:
        return '这个问题需要老板确认。店铺暂未填写联系方式，请稍后再试。'
    return '\n'.join(lines)


def _faq_answer(faq, text):
    """Return the merchant's answer when *text* matches a stored Q/A pair.

    WeChat stores FAQ entries as ordinary merchant text (for example
    ``问：可以安排货代吗？答：可以安排。``).  Treating the field as a
    label-only fact meant the customer bot could never use those answers.
    Keep matching deliberately conservative: exact text after punctuation
    and whitespace normalization, or one side containing the other.
    """
    raw = str(faq or '').strip()
    if not raw:
        return None

    def compact(value):
        value = re.sub(r'[\s，。！？、,.!?：:；;“”"\'‘’（）()]+', '', str(value)).casefold()
        # These are conversational fillers, not business nouns.  Removing
        # them lets “可以帮我安排货代吗” match a stored “可以安排货代吗”.
        return re.sub(r'请问|请|能不能|能否|是否|可以|帮我|一下|吗|呢|有无|有没有', '', value)

    query = compact(text)
    if not query:
        return None
    # Support several entries in one field, separated by a new question.
    pairs = re.findall(
        r'(?:问|问题)\s*[:：]\s*(.*?)\s*(?:答|回答)\s*[:：]\s*'
        r'(.*?)(?=(?:\n\s*)?(?:问|问题)\s*[:：]|$)', raw, flags=re.S)
    for question, answer in pairs:
        q = compact(question)
        if q and (q == query or q in query or query in q):
            return answer.strip()
    return None


def shop_answer(conn, text):
    profile = get_shop(conn)
    faq_reply = _faq_answer(profile.get('faq', ''), text)
    if faq_reply:
        return faq_reply
    for field, label, words in (
        ('shop_name', '档口名称', ('什么店', '档口名称', '店名', '哪家档口')),
        ('address', '店铺地址', ('在哪', '地址', '位置', '哪条街', '怎么走')),
        ('business_hours', '营业时间', ('营业时间', '几点开', '几点关', '开门', '关门')),
        ('shipping_info', '发货物流说明', ('物流', '发货', '运费', '快递')),
        ('faq', '店铺常见问答', ('常见问题', '常见问答', 'FAQ')),
    ):
        if any(word in text for word in words):
            value = profile.get(field, '')
            return f'{label}：{value}' if value else f'店铺尚未填写{label}。' + contact_reply(conn)
    return None
