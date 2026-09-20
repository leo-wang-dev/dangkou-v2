"""E2E 真链路：真实 BAILIAN（视觉抽取+persona 判定）；TG 真收发（有 chat_id 才跑，否则 BLOCKED）。

受阻（blocked）不是失败：环境缺件时如实记录，不冒充绿。
"""
import json
import os
import sqlite3
from pathlib import Path
from catalog import config

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHOTO = os.environ.get('CS_TEST_PHOTO', '')
TG_CHAT = os.environ.get('TG_TEST_CHAT_ID', '')


def _bot():
    from catalog import db
    from catalog.csbot import CsBot
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    from catalog import llm
    return CsBot(conn, _NoApi(), llm=llm), conn


class _NoApi:
    def send_message(self, chat_id, text):
        pass

    def download_photo(self, photo):
        return open(PHOTO, 'rb').read()


_PHOTO_FIXTURE = json.loads((Path(REPO)/'tests/fixtures/customer_photos.json').read_text())
_PHOTO_DIR = Path(os.environ.get('CS_SAMPLE_PHOTO_DIR', _PHOTO_FIXTURE['source_dir']))
_PHOTOS_READY = all((_PHOTO_DIR/c['file']).is_file() for c in _PHOTO_FIXTURE['cases'])


@pytest.mark.skipif(not config.BAILIAN_API_KEY or not _PHOTOS_READY, reason='BLOCKED: 缺 BAILIAN_API_KEY 或四张客户原图')
def test_real_vision_extraction_matches_foreknown(tmp_path):
    """Four supplied original photos, actual model, foreground/price checks, edits and export."""
    from scripts.verify_customer_photos import run
    result=run(tmp_path/'real-photos',real_model=True)
    assert result['mode']=='real-model-local-TG'
    assert result['confirmed_items']==5 and result['embedded_excel_images']==5


@pytest.mark.skipif(not config.BAILIAN_API_KEY, reason='BLOCKED: 缺 BAILIAN_API_KEY')
def test_real_persona_transfer_and_pass():
    """真实文本模型 × 默认红线知识：超量询底价→TRANSFER；普通问候→正常回复。"""
    bot, _ = _bot()
    # 强触发：超量询底价（默认红线明列）——真实模型判定
    reply = bot._on_text({'id': '1', 'tg_name': 't'}, '这个1000个最低多少钱？能便宜到什么程度')
    assert '老板' in reply, '已知红线场景必须转人工'
    # 二选一都接受：模型判 TRANSFER（顶话术）或直接答复——记录行为，不硬编
    print(f"[real-persona] 触发问回复: {reply[:60]}")
    reply2 = bot._on_text({'id': '1', 'tg_name': 't'}, '你好，请问你们卖什么品类？')
    assert '老板' not in reply2                        # 普通问候不应转人工
    print(f"[real-persona] 问候回复: {reply2[:60]}")


@pytest.mark.skipif(not TG_CHAT or not os.environ.get('TG_BOT_TOKEN') or os.environ.get('RUN_REAL_TG') != '1', reason='BLOCKED: 需 TG_BOT_TOKEN、TG_TEST_CHAT_ID、RUN_REAL_TG=1 显式开启真实发送')
def test_real_tg_send_and_poll():
    """TG 真收发：给真实 chat 发消息 + 长轮询收到即回。"""
    from catalog.tg import TgApi
    api = TgApi()
    r = api.send_message(TG_CHAT, '【E2E】清单链接功能上线测试，请忽略～')
    assert r['message_id']
    ups = api.poll(timeout=10)
    print(f"[real-tg] poll {len(ups)} updates")

@pytest.mark.skipif(not config.BAILIAN_API_KEY, reason='BLOCKED: 缺 BAILIAN_API_KEY')
def test_real_merchant_opt_in_conditions():
    from catalog import merchant_policy
    bot,conn=_bot()
    cust=bot._ensure_customer({'id':1234567,'first_name':'merchant-policy-test'})
    conn.execute("UPDATE shop_profile SET owner_wechat='fixture_owner' WHERE id=1")
    merchant_policy.apply(conn,{'shop_name':'规则测试','payment':'只有要求月结超过30天才转人工，30天以内不触发'},1)
    assert 'fixture_owner' in bot._on_text(cust,'可以月结60天吗？')
    assert 'fixture_owner' not in bot._on_text(cust,'可以月结15天吗？')
    merchant_policy.apply(conn,{'shop_name':'规则测试'},2)
    assert 'fixture_owner' not in bot._on_text(cust,'可以月结60天吗？')
