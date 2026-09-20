"""BAILIAN 兼容端点客户端（S2 定案：persona=qwen3.8-max / 抽取=qwen3-vl-plus）。

测试用 monkeypatch 替换 chat_text / chat_vision，不打网络。
"""
import base64
import os

import requests

from . import config

TEXT_MODEL = 'qwen3.8-max'
VISION_MODEL = 'qwen3-vl-plus'
VISION_FALLBACK_MODEL = os.environ.get('BAILIAN_VISION_FALLBACK_MODEL', 'qwen-vl-max')
_TIMEOUT = 180


class EmptyModelResponse(ValueError):
    """The provider accepted the request but produced no usable text."""


def _chat(payload: dict) -> str:
    # The provider can return HTTP 200 with empty content and finish_reason=stop.
    # Retry once; never mark an empty provider response as a successful extraction.
    for _ in range(2):
        r = requests.post(
            f'{config.BAILIAN_BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {config.BAILIAN_API_KEY}'},
            json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
        content = r.json()['choices'][0]['message']['content']
        if isinstance(content, str) and content.strip():
            return content
    raise EmptyModelResponse('模型连续返回空内容，请稍后重试')


def chat_text(system: str, messages: list, model: str = TEXT_MODEL,
              temperature: float = 0.3) -> str:
    return _chat({'model': model, 'temperature': temperature,
                  'messages': [{'role': 'system', 'content': system}, *messages]})


def chat_vision(prompt: str, image_bytes: bytes, model: str = VISION_MODEL) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    payload = {'model': model, 'temperature': 0.1, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}]}]}
    try:
        return _chat(payload)
    except EmptyModelResponse:
        if not VISION_FALLBACK_MODEL or VISION_FALLBACK_MODEL == model:
            raise
        return _chat({**payload, 'model': VISION_FALLBACK_MODEL})


def compress(text: str) -> str:
    """红线原文过长时的总结压缩（cs.summarize 调）。"""
    return chat_text(
        '你是规则整理器。把商家口述的红线压成条目式短文，保留全部数字与阈值，不超过400字。',
        [{'role': 'user', 'content': text}], temperature=0.1)
