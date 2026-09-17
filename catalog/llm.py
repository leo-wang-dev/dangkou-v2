"""BAILIAN 兼容端点客户端（S2 定案：persona=qwen3.8-max / 抽取=qwen3-vl-plus）。

测试用 monkeypatch 替换 chat_text / chat_vision，不打网络。
"""
import base64

import requests

from . import config

TEXT_MODEL = 'qwen3.8-max'
VISION_MODEL = 'qwen3-vl-plus'
_TIMEOUT = 180


def _chat(payload: dict) -> str:
    r = requests.post(
        f'{config.BAILIAN_BASE_URL}/chat/completions',
        headers={'Authorization': f'Bearer {config.BAILIAN_API_KEY}'},
        json=payload, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content']


def chat_text(system: str, messages: list, model: str = TEXT_MODEL,
              temperature: float = 0.3) -> str:
    return _chat({'model': model, 'temperature': temperature,
                  'messages': [{'role': 'system', 'content': system}, *messages]})


def chat_vision(prompt: str, image_bytes: bytes, model: str = VISION_MODEL) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return _chat({'model': model, 'temperature': 0.1, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}]}]})


def compress(text: str) -> str:
    """红线原文过长时的总结压缩（cs.summarize 调）。"""
    return chat_text(
        '你是规则整理器。把商家口述的红线压成条目式短文，保留全部数字与阈值，不超过400字。',
        [{'role': 'user', 'content': text}], temperature=0.1)
