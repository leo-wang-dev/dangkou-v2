"""TG 传输（S1 定案：requests + 重试；长轮询纯出站，免 webhook/域名）。"""
import os
import time

import requests


class TgError(Exception):
    pass


class TgPermanentPhotoError(TgError):
    """The same photo bytes will always be rejected, so retrying blocks the queue."""
    pass


def _permanent_photo_rejection(description):
    value = str(description or '').casefold()
    return any(marker in value for marker in (
        'photo_invalid_dimensions', 'image_process_failed', 'file is too big',
        'photo should be', 'wrong file type', 'invalid file',
    ))


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

    def send_document(self, chat_id, filename, content, caption=''):
        """Upload an Excel attachment; durable outbox owns retry on failure."""
        if len(content) > 49 * 1024 * 1024:
            raise TgError('Excel 文件超过 TG 发送限制，请分批导出')
        try:
            r = self.s.post(f'{self.base}/bot{self.token}/sendDocument',
                            data={'chat_id': str(chat_id), 'caption': caption[:1024]},
                            files={'document': (filename, content,
                                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                            timeout=90)
            body = r.json()
            if not body.get('ok'):
                raise TgError('sendDocument: ' + str(body.get('description', '')).replace(self.token, '[REDACTED]'))
            return body['result']
        except requests.RequestException:
            raise TgError('sendDocument 网络异常，文件保留重试') from None

    def send_photo(self, chat_id, filename, content, caption=''):
        """Upload a merchant product photo without exposing a public image URL."""
        if len(content) > 10 * 1024 * 1024:
            raise TgPermanentPhotoError('商品图片超过 TG 发送限制')
        try:
            r = self.s.post(f'{self.base}/bot{self.token}/sendPhoto',
                            data={'chat_id': str(chat_id), 'caption': caption[:1024]},
                            files={'photo': (filename, content, 'application/octet-stream')},
                            timeout=90)
            body = r.json()
            if not body.get('ok'):
                description = str(body.get('description', '')).replace(self.token, '[REDACTED]')
                if _permanent_photo_rejection(description):
                    raise TgPermanentPhotoError('sendPhoto: ' + description)
                raise TgError('sendPhoto: ' + description)
            return body['result']
        except requests.RequestException:
            raise TgError('sendPhoto 网络异常，图片保留重试') from None

    # ---- 收 ----
    def poll(self, timeout=25):
        """长轮询一轮；调用方持久化后推进 offset，避免丢消息。"""
        params = {'timeout': timeout, 'allowed_updates': ['message']}
        if self._offset is not None:
            params['offset'] = self._offset
        ups = self._call('getUpdates', params, timeout=timeout + 10)
        return ups

    # ---- 发 ----
    def send_message(self, chat_id, text):
        return self._call('sendMessage', {'chat_id': chat_id, 'text': text[:4000]})

    def download_photo(self, photo_sizes) -> bytes:
        """取最大尺寸的图。photo_sizes = message['photo'] 数组。"""
        best = max(photo_sizes, key=lambda p: p.get('width', 0))
        f = self._call('getFile', {'file_id': best['file_id']})
        url = f"{self.base}/file/bot{self.token}/{f['file_path']}"
        try:
            r = self.s.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            raise TgError("下载 Telegram 图片失败，稍后重试") from None
