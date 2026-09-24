"""H5 网页客服：复用 CsBot 内核，回复随 HTTP 返回（替代 TG 传输层）。

与 TG 模式的差别只有传输：出站 tg* 渠道一律丢弃（清单文件走页面
/cs/link/{token}/export.xlsx 直出），notify 渠道照旧——商家仍在微信收提醒。
"""
import secrets

from . import cs_i18n
from .csbot import CsBot


class H5Bot(CsBot):
    """api=None：不主动外发；一切回复由调用方（HTTP 路由）直接拿到。"""

    def _enqueue(self, channel, recipient, body):
        if channel in ('tg', 'tg_document', 'tg_photo'):
            return
        super()._enqueue(channel, recipient, body)


def ensure_visitor(bot: H5Bot, visitor: str) -> dict:
    """访客 id（页面 localStorage 生成）→ 稳定 customer 行。"""
    visitor = ''.join(ch for ch in str(visitor or '') if ch.isalnum() or ch == '-')[:40]
    if not visitor.startswith('h5-'):
        visitor = 'h5-' + (visitor or secrets.token_hex(6))
    return bot._ensure_customer({'id': visitor})


def set_language(conn, cust: dict, lang: str) -> str:
    lang = lang if lang in ('中文', 'English') else '中文'
    cs_i18n.set_language(conn, cust['id'], lang)
    conn.commit()
    return lang
