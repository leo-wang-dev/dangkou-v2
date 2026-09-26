"""H5 网页客服：CsBot 内核 + HTTP 路由入口（TG 传输拆除后的唯一 C 端入口）。

api=None：不主动外发；一切回复由调用方（HTTP 路由）直接拿到。
商家提醒仍走 notify* 渠道（微信）；清单文件走 /cs/link/{token}/export.xlsx
页面导出，不再有出站文件渠道。
"""
import secrets

from . import cs_i18n
from .csbot import CsBot


class H5Bot(CsBot):
    """H5 直调内核：与 CsBot 完全同体，仅约定 api=None（无外发传输）。"""


def ensure_visitor(bot: H5Bot, visitor: str) -> dict:
    """访客 id（页面 localStorage 生成）→ 稳定 customer 行。"""
    return bot._ensure_customer({'id': normalize_visitor(visitor)})


def normalize_visitor(visitor: str) -> str:
    """页面 visitor 原始串 → cs_customer.tg_id 同一口径（ensure_visitor 的只读半段）。"""
    visitor = ''.join(ch for ch in str(visitor or '') if ch.isalnum() or ch == '-')[:40]
    if not visitor.startswith('h5-'):
        visitor = 'h5-' + (visitor or secrets.token_hex(6))
    return visitor


def lookup_visitor(conn, visitor: str):
    """只读解析访客 → customer 行（不存在返回 None，不落库；GET 清单令牌用）。"""
    return conn.execute('SELECT * FROM cs_customer WHERE tg_id=?',
                        (normalize_visitor(visitor),)).fetchone()


def set_language(conn, cust: dict, lang: str) -> str:
    lang = lang if lang in ('中文', 'English') else '中文'
    cs_i18n.set_language(conn, cust['id'], lang)
    conn.commit()
    return lang
