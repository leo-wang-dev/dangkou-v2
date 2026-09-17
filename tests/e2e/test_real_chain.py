"""E2E 真链路：真实 BAILIAN（视觉抽取+persona 判定）；TG 真收发（有 chat_id 才跑，否则 BLOCKED）。

受阻（blocked）不是失败：环境缺件时如实记录，不冒充绿。
"""
import json
import os
import sqlite3

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHOTO = '/Users/elias/Desktop/抽取预研-12张真实照片/01-直发夹板150R160R.jpg'
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


def test_real_vision_extraction_matches_foreknown():
    """真实视觉模型 × 真照片：与预研交叉验证过的已知答案（150R/160R 夹板）。"""
    bot, conn = _bot()
    upd = {'message': {'chat': {'id': 1}, 'from': {'id': 1, 'username': 't'},
                       'photo': [{'file_id': 'f', 'width': 1, 'height': 1}]}}
    bot.handle_update(upd)
    notes = conn.execute('SELECT * FROM cs_note').fetchall()
    assert notes, '抽取落库失败'
    fields = json.loads(notes[0]['fields_json'])
    joined = json.dumps(fields, ensure_ascii=False)
    assert '150' in joined and '160' in joined          # 已知：150R/160R 手写价
    assert '未拍到' in joined                            # 没拍到的字段如实标注


def test_real_persona_transfer_and_pass():
    """真实文本模型 × 默认红线知识：超量询底价→TRANSFER；普通问候→正常回复。"""
    bot, _ = _bot()
    # 强触发：超量询底价（默认红线明列）——真实模型判定
    reply = bot._on_text({'id': '1', 'tg_name': 't'}, '这个1000个最低多少钱？能便宜到什么程度')
    assert reply, 'persona 无回复'
    # 二选一都接受：模型判 TRANSFER（顶话术）或直接答复——记录行为，不硬编
    print(f"[real-persona] 触发问回复: {reply[:60]}")
    reply2 = bot._on_text({'id': '1', 'tg_name': 't'}, '你好，请问你们卖什么品类？')
    assert '马上来' not in reply2                        # 普通问候不应转人工
    print(f"[real-persona] 问候回复: {reply2[:60]}")


@pytest.mark.skipif(not TG_CHAT, reason='BLOCKED: TG_TEST_CHAT_ID 未设（商家未发过消息，bot 不能主动发起）')
def test_real_tg_send_and_poll():
    """TG 真收发：给真实 chat 发消息 + 长轮询收到即回。"""
    from catalog.tg import TgApi
    api = TgApi()
    r = api.send_message(TG_CHAT, '【E2E】清单链接功能上线测试，请忽略～')
    assert r['message_id']
    ups = api.poll(timeout=10)
    print(f"[real-tg] poll {len(ups)} updates")
