"""TG 传输（S1 定案：requests + 重试；长轮询纯出站，免 webhook/域名）。"""
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
        self._offset = None

    def _call(self, method, params=None, timeout=None):
        for attempt in (1, 2, 3):                     # S1：SSL 偶发截断，3 次重试兜底
            try:
                r = self.s.post(f'{self.base}/bot{self.token}/{method}',
                                json=params or {}, timeout=timeout or 35)
                body = r.json()
                if not body.get('ok'):
                    raise TgError(f'{method}: {body.get("description")}')
                return body['result']
            except (requests.RequestException, TgError):
                if attempt == 3:
                    raise
                time.sleep(2 * attempt)

    # ---- 收 ----
    def poll(self, timeout=25):
        """长轮询一轮；内部维护 offset，确认过的不再重收。"""
        params = {'timeout': timeout, 'allowed_updates': ['message']}
        if self._offset is not None:
            params['offset'] = self._offset
        ups = self._call('getUpdates', params, timeout=timeout + 10)
        if ups:
            self._offset = ups[-1]['update_id'] + 1
        return ups

    # ---- 发 ----
    def send_message(self, chat_id, text):
        return self._call('sendMessage', {'chat_id': chat_id, 'text': text[:4000]})

    def download_photo(self, photo_sizes) -> bytes:
        """取最大尺寸的图。photo_sizes = message['photo'] 数组。"""
        best = max(photo_sizes, key=lambda p: p.get('width', 0))
        f = self._call('getFile', {'file_id': best['file_id']})
        url = f"{self.base}/file/bot{self.token}/{f['file_path']}"
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        return r.content
