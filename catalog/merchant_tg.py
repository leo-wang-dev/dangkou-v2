"""B端商家接入面的 TG 传输（多店一店一实例架构保留部分）。

C端 TG 客服传输已整体拆除（删除工单C，2026-09-27）；本模块只服务保留的
商家侧流程：接入助手轮询（run_merchant_hub）与商家自有 bot Token 的
身份核验（merchant_binding.identity）。不再提供客服收发/图片/文档通道。
"""
import os
import time

import requests


class TgError(Exception):
    pass


class TgApi:
    def __init__(self, token=None, base=None):
        self.token = token or os.environ.get('TG_BOT_TOKEN', '')
        self.base = (base or os.environ.get('TG_API_BASE', 'https://api.telegram.org')).rstrip('/')
        if not self.token:
            raise TgError('TG_BOT_TOKEN 未配置')
        self.s = requests.Session()
        proxy = os.environ.get("TG_PROXY_URL", "")
        if proxy:
            self.s.proxies.update({"https": proxy, "http": proxy})
            self.s.trust_env = False
        self._offset = None

    def _call(self, method, params=None, timeout=None):
        for attempt in (1, 2, 3):                     # S1：SSL 偶发截断，3 次重试兜底
            try:
                r = self.s.post(f'{self.base}/bot{self.token}/{method}',
                                json=params or {}, timeout=timeout or 35)
                body = r.json()
                if not body.get('ok'):
                    raise TgError(f'{method}: {body.get("description")}'.replace(self.token, "[REDACTED]"))
                return body['result']
            except (requests.RequestException, TgError) as exc:
                if attempt == 3:
                    if isinstance(exc, TgError):
                        raise
                    raise TgError(f"{method}: Telegram 网络请求失败（{type(exc).__name__}）") from None
                time.sleep(2 * attempt)

    def poll(self, timeout=25):
        """长轮询一轮；调用方持久化后推进 offset，避免丢消息。"""
        params = {'timeout': timeout, 'allowed_updates': ['message']}
        if self._offset is not None:
            params['offset'] = self._offset
        return self._call('getUpdates', params, timeout=timeout + 10)

    def send_message(self, chat_id, text):
        return self._call('sendMessage', {'chat_id': chat_id, 'text': text[:4000]})
