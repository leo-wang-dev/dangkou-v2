"""Non-overridable platform rule: customer price/quantity offers go to the owner."""
import re
import unicodedata

RULE = ('所有涉及价格、单价、总价、成本、多少数量对应多少价格、报价、折扣、议价、运费及支付金额的问题，'
        '一律转人工；不得自动报价、计算价格或承诺价格。平台公共红线，店级与商品级规则均不能覆盖。')


def contains_price_topic(text):
    """Detect explicit price/payment language without treating a note quantity as price."""
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    compact = re.sub(r'[\s\u200b-\u200f\ufeff]+', '', text)
    if any(word in compact for word in (
        '价', '钱', '折', '费用', '行情', '最低', '商量空间', '报价', '询价', '底价',
        '优惠', '便宜', '贵', '怎么卖', '咋卖', '怎么批', '运费', '邮费', '包邮',
        '含税', '税点', '税费', '佣金', '定金', '订金', '尾款', '货款', '付款', '金额', '汇率')):
        return True
    return bool(re.search(
        r'[$¥￥€£]|\b(price|pricing|cost|quote|quotation|discount|cheaper|cheapest|wholesale|'
        r'bargain|how\s+much|freight|shipping\s+(?:cost|fee)|precio|preço|цена|скидка)\b', text))


def requires_handoff(text):
    text = unicodedata.normalize('NFKC',str(text)).casefold()
    compact = re.sub(r'[\s\u200b-\u200f\ufeff]+','',text)
    if any(word in compact for word in (
        '价','钱','折','费用','行情','最低','商量空间','价格','价钱','单价','总价','成本','多少钱','多少米','几钱','几块','几元','几毛',
        '报价','询价','底价','拿货价','批发价','优惠','便宜','折扣','打折','折后','议价',
        '砍价','降价','贵了','太贵','贵不贵','怎么卖','咋卖','怎么批','运费','邮费','包邮',
        '含税','税点','税费','佣金','定金','订金','尾款','货款','付款','支付金额','汇率',
        '批量','大货价','多少起批','多拿','多买','少拿','起批价','军火','弹药','违禁品')):
        return True
    if re.search(r'[$¥￥€£]|\d+(?:\.\d+)?\s*(?:元|块|毛|角|美金|美元|人民币|usd|rmb|eur|cny|dollars?)',text):
        return True
    if re.fullmatch(r'(?:第?\d+条?)?(?:起订量|装箱数|采购数量)(?:改成|改为|设为|记为|补充为)?[一二三四五六七八九十百千万两\d]+(?:箱|个|件|pcs)(?:起)?',compact):
        return False  # explicit non-price notebook bookkeeping, not an offer
    if re.search(r'(?:\d+|[一二三四五六七八九十百千万两]+)\s*(?:个|件|只|支|台|把|瓶|盒|罐|箱|套|包|pcs\b|pieces?\b|units?\b)',text):
        return True
    if re.search(r'\b(price|pricing|cost|quote|quotation|discount|cheaper|cheapest|wholesale|bargain|how\s+much|freight|shipping\s+(?:cost|fee)|precio|preço|цена|скидка)\b',text):
        return True
    # A follow-up amount/quantity must not become a hidden quote path.
    return bool(re.fullmatch(r'(?:那|要|拿|买)?\s*\d+(?:\.\d+)?\s*(?:呢|行吗|可以吗|怎么样|[?？])?',text))


def price_field(field):
    field=unicodedata.normalize('NFKC',str(field)).casefold()
    return any(k in field for k in ('价','金额','成本','运费','折扣','price','cost','amount','discount'))


def is_legacy_auto_quote(text):
    """Old durable messages must not send a discontinued automatic quote on retry."""
    return bool(re.search(r'对应单价\s*[¥￥]|确认数量后按档位报价',str(text)))


def contains_price_amount(text):
    """Prevent configured FAQ/shipping text from becoming a backdoor quotation."""
    text=unicodedata.normalize('NFKC',str(text)).casefold()
    return bool(re.search(r'[$¥￥€£]\s*\d|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)\s*(?:元|块|毛|角|美金|美元|人民币|usd\b|rmb\b|eur\b|cny\b|dollars?\b)|(?:单价|价格|报价|金额|price|cost)\s*[:：=]?\s*\d',text))


def contains_link(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    return bool(re.search(
        r'(?:https?://|www\.|t\.me/|telegram\.me/|weixin://|'
        r'(?<![\w@])(?:[a-z0-9-]+\.)+[a-z]{2,63}(?:[/\s]|$))', text))


def public_spec_allowed(label, value):
    """Defense in depth for every customer-facing catalog projection."""
    normalized = unicodedata.normalize('NFKC', str(label)).casefold()
    if price_field(normalized) or any(word in normalized for word in ('链接', '网址', 'url', 'link')):
        return False
    return not contains_price_amount(value) and not contains_link(value)
