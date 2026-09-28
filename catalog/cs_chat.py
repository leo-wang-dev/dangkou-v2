"""H5 网页客服：CsBot 内核 + HTTP 路由入口（TG 传输拆除后的唯一 C 端入口）。

api=None：不主动外发；一切回复由调用方（HTTP 路由）直接拿到。
商家提醒仍走 notify* 渠道（微信）；清单文件走 /cs/link/{token}/export.xlsx
页面导出，不再有出站文件渠道。
"""
import json
import os
import secrets
import sqlite3

from . import cs_i18n
from .csbot import CsBot


class H5Bot(CsBot):
    """H5 直调内核：与 CsBot 完全同体，仅约定 api=None（无外发传输）。"""


class _ReplayMiss(BaseException):
    """A changed snapshot needs another planning pass, never a live model call."""


class _CachedModel:
    def __init__(self, model, cache, replay=False):
        self.model = model
        self.cache = cache
        self.replay = replay

    def __getattr__(self, name):
        value = getattr(self.model, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            key = (name, json.dumps([args, kwargs], sort_keys=True,
                                    ensure_ascii=False, default=str))
            if key not in self.cache:
                if self.replay:
                    raise _ReplayMiss()
                try:
                    self.cache[key] = (True, value(*args, **kwargs))
                except Exception as exc:
                    self.cache[key] = (False, exc)
            succeeded, result = self.cache[key]
            if not succeeded:
                raise result
            return result

        return call


class _CachedConnection:
    """Proxy SQLite and freeze remote catalog reads during a planned turn."""

    def __init__(self, connection, cache, replay=False):
        self.connection = connection
        self.cache = cache
        self.replay = replay

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def customer_catalog_remote(self, path, body=None):
        from . import customer_catalog, shop_link

        key = (shop_link.profile(self.connection)['shop_id'],
               os.environ.get('CATALOG_CS_API_URL', ''),
               os.environ.get('CATALOG_CS_SERVICE_TOKEN', ''), path,
               json.dumps(body, sort_keys=True, ensure_ascii=False, default=str))
        if key not in self.cache:
            if self.replay:
                raise _ReplayMiss()
            try:
                self.cache[key] = (True, customer_catalog._remote_uncached(
                    self.connection, path, body))
            except Exception as exc:
                self.cache[key] = (False, exc)
        succeeded, result = self.cache[key]
        if not succeeded:
            raise result
        return result


def text_turn_transaction(conn, visitor: str, text: str, model, attempts=3) -> str:
    """Plan model calls on a private snapshot; atomically replay on current data.

    The real connection takes its writer lock only after all model calls finish.
    A changed database is replanned, while identical model prompts reuse results.
    """
    from fastapi import HTTPException

    model_cache = {}
    catalog_cache = {}
    visitor = normalize_visitor(visitor)
    for _ in range(attempts):
        before = conn.execute('PRAGMA data_version').fetchone()[0]
        snapshot = sqlite3.connect(':memory:', check_same_thread=False)
        snapshot.row_factory = sqlite3.Row
        try:
            conn.backup(snapshot)
            version = conn.execute('PRAGMA data_version').fetchone()[0]
            if version != before:
                continue
            bot = H5Bot(_CachedConnection(snapshot, catalog_cache), api=None,
                        llm=_CachedModel(model, model_cache))
            bot._processing = True
            cust = ensure_visitor(bot, visitor)
            bot._text_turn(cust, text)
        finally:
            snapshot.close()

        conn.execute('PRAGMA busy_timeout=500')
        try:
            conn.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY,
                                                         sqlite3.SQLITE_LOCKED):
                raise HTTPException(409, '会话正在更新，请重试本条消息') from exc
            raise
        if conn.execute('PRAGMA data_version').fetchone()[0] != version:
            conn.rollback()
            continue
        try:
            bot = H5Bot(_CachedConnection(conn, catalog_cache, replay=True), api=None,
                        llm=_CachedModel(model, model_cache, replay=True))
            bot._processing = True
            cust = ensure_visitor(bot, visitor)
            return bot._text_turn(cust, text)
        except _ReplayMiss:
            conn.rollback()
            continue
        except BaseException:
            conn.rollback()
            raise
    raise HTTPException(409, '会话已更新，请重试本条消息')


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
    cs_i18n.set_language(conn, cust['id'], lang, commit=False)
    return lang
