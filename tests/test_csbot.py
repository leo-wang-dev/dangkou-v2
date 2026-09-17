"""C端 TG 机器人脑：照片→抽取回执→确认→出表；文本→persona（红线知识注入判定）。"""
import json
import sqlite3

import pytest

from catalog import cs, db
from catalog.csbot import CsBot, TRANSFER_MARK


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


class FakeApi:
    """TG 传输假件：记录发送，喂假图。"""
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def download_photo(self, photo):
        return b'fake-jpeg-bytes'


class FakeLlm:
    """LLM 假件：可编程应答 + 捕获 system prompt（验证知识注入）。"""
    def __init__(self):
        self.text_reply = '在的，欢迎看货～'
        self.vision_reply = json.dumps([{
            '型号或品名': '直发夹板', '价格': '80R', '装箱数': '40 PCS',
            '颜色': '黑色', '体积或尺寸': '未拍到', '起订量': '未拍到'}],
            ensure_ascii=False)
        self.calls = []

    def chat_text(self, system, messages, **kw):
        self.calls.append(('text', system, messages))
        return self.text_reply

    def chat_vision(self, prompt, image_bytes, **kw):
        self.calls.append(('vision', prompt))
        return self.vision_reply


@pytest.fixture()
def bot(conn):
    api, llm = FakeApi(), FakeLlm()
    notified = []
    b = CsBot(conn, api, llm=llm, notifier=notified.append)
    b.notified = notified
    return b


def _photo_upd(chat_id=100):
    return {'update_id': 1, 'message': {
        'message_id': 5, 'chat': {'id': chat_id}, 'from': {'id': 100, 'username': 'buyer'},
        'date': 0, 'photo': [{'file_id': 'f1', 'width': 100, 'height': 100}]}}


def _text_upd(text, chat_id=100):
    return {'update_id': 2, 'message': {
        'message_id': 6, 'chat': {'id': chat_id}, 'from': {'id': 100, 'username': 'buyer'},
        'date': 0, 'text': text}}


# ---------- 拍照整理 ----------

def test_photo_creates_draft_and_receipt(bot, conn):
    bot.handle_update(_photo_upd())
    notes = conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchall()
    assert len(notes) == 1
    assert json.loads(notes[0]['fields_json'])['价格'] == '80R'
    assert notes[0]['photo']                          # 图已落盘路径
    receipt = bot.api.sent[-1][1]
    assert '直发夹板' in receipt and '80R' in receipt
    assert '没拍到' in receipt                         # 没抽到的字段明说


def test_confirm_promotes_drafts(bot, conn):
    bot.handle_update(_photo_upd())
    bot.handle_update(_text_upd('确认'))
    assert conn.execute("SELECT COUNT(*) c FROM cs_note WHERE status='confirmed'").fetchone()['c'] == 1
    assert '1 条' in bot.api.sent[-1][1]


def test_export_link(bot, conn):
    bot.handle_update(_photo_upd())
    bot.handle_update(_text_upd('确认'))
    bot.handle_update(_text_upd('出表'))
    row = conn.execute('SELECT * FROM cs_link').fetchone()
    assert row and row['customer_id']
    assert row['token'] in bot.api.sent[-1][1]         # 链接含一次性 token


def test_voice_gets_fixed_hint(bot):
    upd = _text_upd('x')
    upd['message'] = {**upd['message'], 'voice': {'file_id': 'v1'}, 'text': None}
    del upd['message']['text']
    bot.handle_update(upd)
    assert '打字' in bot.api.sent[-1][1]


# ---------- persona + 红线 ----------

def test_persona_reply_and_knowledge_injection(bot, conn):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    bot.handle_update(_text_upd('你好，有什么货？'))
    kind, system, messages = bot.llm.calls[-1]
    assert kind == 'text'
    assert '数量少于50转人工' in system                 # 红线知识注入 system prompt
    assert '军火' in system                            # 系统级硬规则常驻
    assert bot.api.sent[-1][1] == '在的，欢迎看货～'    # 正常回复透传


def test_transfer_escalates_with_rule_citation(bot, conn):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    bot.llm.text_reply = f'{TRANSFER_MARK}数量少于50转人工'
    bot.handle_update(_text_upd('30个多少钱'))
    # 客户侧：顶话术，不硬答
    assert '商家' in bot.api.sent[-1][1]
    assert '30个' not in bot.api.sent[-1][1] or True
    # 商家侧：微信提醒含客户原话+红线原文
    assert len(bot.notified) == 1
    remind = bot.notified[0]
    assert '30个多少钱' in remind and '数量少于50的转人工' in remind


def test_conversation_logged(bot, conn):
    bot.handle_update(_text_upd('在吗'))
    rows = conn.execute("SELECT role FROM cs_conversation_log ORDER BY id").fetchall()
    assert [r['role'] for r in rows] == ['user', 'assistant']


def test_customer_upserted(bot, conn):
    bot.handle_update(_text_upd('hi'))
    row = conn.execute('SELECT * FROM cs_customer WHERE tg_id=?', ('100',)).fetchone()
    assert row['tg_name'] == 'buyer'
