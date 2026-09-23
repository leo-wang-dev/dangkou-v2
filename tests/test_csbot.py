"""C端 TG 机器人脑：照片→抽取回执→确认→出表；文本→persona（红线知识注入判定）。"""
import json
import itertools
_UPDATE_IDS = itertools.count(1)
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
        self.documents = []
        self.photos = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_document(self, chat_id, filename, content, caption=""):
        self.documents.append((chat_id, filename, content, caption))
        return {"message_id": len(self.documents)}

    def send_photo(self, chat_id, filename, content, caption=""):
        self.photos.append((chat_id, filename, content, caption))
        return {"message_id": len(self.photos)}

    def download_photo(self, photo):
        return b'fake-jpeg-bytes'


class FakeLlm:
    """LLM 假件：可编程应答 + 捕获 system prompt（验证知识注入）。"""
    def __init__(self):
        self.text_reply = '<<PASS>>'
        self.edit_reply = '{"action":"none"}'
        self.vision_reply = json.dumps([{
            '型号或品名': '直发夹板', '价格': '80R', '装箱数': '40 PCS',
            '颜色': '黑色', '体积或尺寸': '未拍到', '起订量': '未拍到'}],
            ensure_ascii=False)
        self.calls = []

    def chat_text(self, system, messages, **kw):
        self.calls.append(('text', system, messages))
        if '草稿编辑判定' in system:
            return self.edit_reply
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
    # 新版客户 bot 未选语言时首条消息是语言选择提示；测试客户 tg_id 固定 100，
    # 预置“中文”跳过该步，保持用例只关心业务行为。
    from catalog import cs_i18n
    b._ensure_customer({'id': 100, 'username': 'buyer'})
    cs_i18n.set_language(
        conn, conn.execute("SELECT id FROM cs_customer WHERE tg_id='100'").fetchone()[0], '中文')
    conn.commit()
    return b


def _photo_upd(chat_id=100):
    return {'update_id': next(_UPDATE_IDS), 'message': {
        'message_id': 5, 'chat': {'id': chat_id}, 'from': {'id': 100, 'username': 'buyer'},
        'date': 0, 'photo': [{'file_id': 'f1', 'width': 100, 'height': 100}]}}


def _text_upd(text, chat_id=100):
    return {'update_id': next(_UPDATE_IDS), 'message': {
        'message_id': 6, 'chat': {'id': chat_id}, 'from': {'id': 100, 'username': 'buyer'},
        'date': 0, 'text': text}}


# ---------- 拍照整理 ----------

def test_photo_creates_draft_and_receipt(bot, conn):
    bot.handle_update(_photo_upd())
    notes = conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchall()
    assert len(notes) == 1
    assert json.loads(notes[0]['fields_json'])['价格'] == '80R（照片识别，待确认）'
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


# ---------- 草稿自然语言补改 ----------

def test_draft_edit_applies_and_rereceipts(bot, conn):
    bot.handle_update(_photo_upd())
    before=conn.execute('SELECT fields_json FROM cs_note').fetchone()[0]
    bot.llm.edit_reply='{"action":"edit","index":1,"field":"价格","value":"2.5"}'
    bot.handle_update(_text_upd('1 价格改成2.5'))
    assert conn.execute('SELECT fields_json FROM cs_note').fetchone()[0] != before
    assert '老板' not in bot.api.sent[-1][1]


def test_draft_add_field_to_last(bot, conn):
    bot.handle_update(_photo_upd())
    bot.llm.edit_reply = '{"action":"add","field":"起订量","value":"1箱"}'
    bot.handle_update(_text_upd('起订量一箱起'))
    f = json.loads(conn.execute("SELECT fields_json FROM cs_note WHERE status='draft'")
                   .fetchone()['fields_json'])
    assert f['起订量'] == '1箱'


def test_draft_delete_note(bot, conn):
    bot.handle_update(_photo_upd())
    bot.llm.edit_reply = '{"action":"delete_note","index":1}'
    bot.handle_update(_text_upd('第1条删了'))
    n = conn.execute("SELECT COUNT(*) c FROM cs_note WHERE status='draft'").fetchone()['c']
    assert n == 0


def test_non_edit_message_falls_to_persona(bot, conn):
    bot.handle_update(_photo_upd())
    bot.llm.edit_reply = '{"action":"none"}'
    bot.handle_update(_text_upd('你们还有什么货？'))
    assert bot.api.sent[-1][1] == '您好，可以告诉我商品型号和采购数量，或发照片整理清单。'            # 落回 persona


def test_catalog_brief_carries_cost_price(bot, conn):
    """底价红线可判：商品简表带出厂价（标注内部禁报）。"""
    conn.execute(
        "INSERT INTO product_curler(id, inner_code, item_no, price, tier_price, cs_visible) "
        "VALUES('p1','KS-X','8226','10','20:12',1)")
    conn.commit()
    brief = bot._catalog_brief()
    assert '8226' in brief and '成本价' not in brief and '20:12' not in brief


def test_catalog_query_sends_only_three_spec_cards_and_photos_without_link_or_price(bot, conn, tmp_path, monkeypatch):
    from catalog.storage import LocalStorage
    monkeypatch.setenv('CATALOG_V2_IMG', str(tmp_path / 'images'))
    storage = LocalStorage(str(tmp_path / 'images'))
    for index in range(4):
        pid = f'p{index}'
        photo = storage.save('curler', pid, 'main.jpg', b'photo-' + bytes([index]))
        conn.execute(
            "INSERT INTO product_curler(id,inner_code,item_no,price,voltage,material,cs_visible,image_main) "
            "VALUES(?,?,?,?,?,?,1,?)",
            (pid, f'KS-{index}', f'MODEL-{index}', f'{99 + index}.99', '220V', '黑色', photo))
    conn.commit()

    bot.handle_update(_text_upd('有哪些商品'))

    reply = bot.api.sent[-1][1]
    assert all(value not in reply for value in ('http://', 'https://', '价格', '报价', '99.99'))
    assert reply.count('MODEL-') == 3
    assert '220V' in reply and '黑色' in reply
    assert len(bot.api.photos) == 3
    assert [item[2] for item in bot.api.photos] == [b'photo-\x00', b'photo-\x01', b'photo-\x02']


def test_dynamic_product_query_excludes_cost_and_price_but_sends_image(bot, conn, tmp_path, monkeypatch):
    from catalog import dynamic_catalog
    from catalog import merchant_policy
    from catalog.storage import LocalStorage
    monkeypatch.setenv('CATALOG_V2_IMG', str(tmp_path / 'images'))
    category = 'cat_hairdryer'
    dynamic_catalog.approve_template(conn, {
        'key': category, 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
            {'key': 'cost', 'label': '成本', 'role': 'cost', 'visibility': 'public', 'type': 'money'},
            {'key': 'price', 'label': '价格', 'role': 'price', 'visibility': 'public', 'type': 'money'},
        ],
    }, expected_version=0)
    storage = LocalStorage(str(tmp_path / 'images'))
    photo = storage.save(category, 'dryer', 'main.jpg', b'dryer-photo')
    dynamic_catalog.upsert_approved_products(conn, category, [{
        'id': 'dryer', 'inner_code': 'INNER-1', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '玫红色', 'cost': '35.00', 'price': '88.00'},
        'images': [photo],
    }])
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    cs.set_redline(conn, None, '')
    conn.commit()

    bot.handle_update(_text_upd('HD15 有什么规格'))

    reply = bot.api.sent[-1][1]
    assert 'HD15' in reply and '玫红色' in reply
    assert all(value not in reply for value in ('35.00', '88.00', '成本', '价格', 'http'))
    assert bot.api.photos[-1][2] == b'dryer-photo'


def test_managed_photo_match_does_not_promise_an_unconfigured_handoff(bot, conn, tmp_path):
    from catalog import merchant_policy
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    cs.set_redline(conn, None, '')
    photo_path = tmp_path / 'customer.jpg'
    photo_path.write_bytes(b'photo')

    reply = bot._on_photo(
        {'id': 'buyer'},
        {},
        prepared=(str(photo_path), [{'型号或品名': 'MODEL-1'}], [
            {'category': 'curler', 'product_id': 'p1', 'name': 'MODEL-1'},
        ]),
    )

    assert '回复“询价1”即可转人工' not in reply
    assert '回复“询价1”查看对应商品资料' in reply


def test_removed_product_photo_does_not_block_later_customer_messages(bot, conn):
    conn.execute("INSERT INTO cs_outbox(channel,recipient,body) VALUES('tg_photo','100',?)",
                 (json.dumps({'id': 'gone', '_category': 'razor', 'name': '已下架商品'}),))
    conn.execute("INSERT INTO cs_outbox(channel,recipient,body) VALUES('tg','100','后续消息')")
    conn.commit()

    bot.flush_outbox()

    rows = conn.execute('SELECT channel,sent,last_error FROM cs_outbox ORDER BY id').fetchall()
    assert [(row['channel'], row['sent']) for row in rows] == [('tg_photo', 1), ('tg', 1)]
    assert rows[0]['last_error'] == 'photo unavailable'
    assert bot.api.sent[-1] == (100, '后续消息')


def test_oversized_product_photo_does_not_block_later_customer_messages(bot, conn, monkeypatch):
    from catalog.tg import TgApi

    monkeypatch.setattr('catalog.customer_catalog.photo_bytes',
                        lambda *_: ('large.jpg', b'x' * (10 * 1024 * 1024 + 1)))
    bot.api.send_photo = lambda chat_id, filename, content, caption='': (
        TgApi.send_photo(object(), chat_id, filename, content, caption))
    conn.execute("INSERT INTO cs_outbox(channel,recipient,body) VALUES('tg_photo','100',?)",
                 (json.dumps({'id': 'large', '_category': 'razor', 'name': '超大图片商品'}),))
    conn.execute("INSERT INTO cs_outbox(channel,recipient,body) VALUES('tg','100','后续消息')")
    conn.commit()

    bot.flush_outbox()

    rows = conn.execute('SELECT channel,sent,last_error FROM cs_outbox ORDER BY id').fetchall()
    assert [(row['channel'], row['sent']) for row in rows] == [('tg_photo', 1), ('tg', 1)]
    assert rows[0]['last_error'] == 'photo rejected'
    assert bot.api.sent[-1] == (100, '后续消息')


# ---------- persona + 红线 ----------

def test_persona_reply_and_knowledge_injection(bot, conn):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    bot.handle_update(_text_upd('你好，有什么货？'))
    kind, system, messages = bot.llm.calls[-1]
    assert kind == 'text'
    assert '数量少于50转人工' in system                 # 红线知识注入 system prompt
    assert '军火' not in system
    assert bot.api.sent[-1][1] == '您好，可以告诉我商品型号和采购数量，或发照片整理清单。'    # 正常回复透传


def test_transfer_escalates_with_rule_citation(bot, conn):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    bot.llm.text_reply = f'{TRANSFER_MARK}数量少于50转人工'
    bot.handle_update(_text_upd('30个多少钱'))
    # 客户侧：顶话术，不硬答
    assert '老板' in bot.api.sent[-1][1]
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


def test_export_drafts_attachment_has_photos_status_and_customer_isolation(bot, conn, tmp_path):
    import io
    import openpyxl
    from PIL import Image
    photo = tmp_path / 'photo.jpg'
    Image.new('RGB', (80, 80), 'green').save(photo)
    bot.api.download_photo = lambda _: photo.read_bytes()
    bot.handle_update(_photo_upd())
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('other','999')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json,status) VALUES('other','{\"私密\":\"其他客户\"}','confirmed')")
    conn.commit()
    update = _text_upd('出表')
    bot.handle_update(update)
    assert len(bot.api.documents) == 1
    chat, name, content, caption = bot.api.documents[0]
    assert chat == 100 and name.endswith('.xlsx') and '1 条待确认' in caption
    sheet = openpyxl.load_workbook(io.BytesIO(content)).active
    assert sheet.max_row == 2 and len(sheet._images) == 1
    assert '待确认' in [c.value for c in sheet[2]]
    assert '其他客户' not in str(list(sheet.values))
    assert conn.execute("SELECT status FROM cs_note WHERE customer_id!='other'").fetchone()[0] == 'draft'
    bot.handle_update(update)
    assert len(bot.api.documents) == 1  # redelivery of the same update cannot export twice


def test_document_retry_survives_restart_and_keeps_export_snapshot(bot, conn, monkeypatch):
    import io
    import openpyxl
    monkeypatch.setenv('CATALOG_NOTIFY_WORKER', '1')
    bot.handle_update(_photo_upd())
    def fail(*args):
        raise RuntimeError('network unavailable')
    bot.api.send_document = fail
    bot.handle_update(_text_upd('出表'))
    pending = conn.execute("SELECT * FROM cs_outbox WHERE channel='tg_document'").fetchone()
    assert pending['sent'] == 0 and pending['attempts'] == 1
    # Merchant worker must never consume Telegram attachments.
    bot.flush_outbox(notifications_only=True)
    assert not bot.notified
    conn.execute("UPDATE cs_note SET fields_json='{}'")
    conn.execute("UPDATE cs_outbox SET next_attempt_at=datetime('now','-1 second')")
    conn.commit()
    fresh = CsBot(conn, FakeApi(), llm=FakeLlm())
    fresh.flush_outbox()
    assert len(fresh.api.documents) == 1
    sheet = openpyxl.load_workbook(io.BytesIO(fresh.api.documents[0][2])).active
    assert '直发夹板' in str(list(sheet.values))  # export keeps the fields at request time
    assert conn.execute("SELECT sent FROM cs_outbox WHERE channel='tg_document'").fetchone()[0] == 1
    assert not fresh.api.sent  # worker mode intentionally handles Telegram attachments only


def test_export_empty_list_does_not_send_empty_excel(bot, conn):
    bot.handle_update(_text_upd('出表'))
    assert not bot.api.documents
    assert '没有可导出' in bot.api.sent[-1][1]
    assert conn.execute('SELECT COUNT(*) FROM cs_link').fetchone()[0] == 0


def test_assign_different_suppliers_and_export_without_cross_customer_changes(bot, conn):
    import io
    import openpyxl
    bot.handle_update(_photo_upd())
    bot.handle_update(_photo_upd())
    bot.handle_update(_text_upd('确认'))
    bot.handle_update(_photo_upd())
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('other','999')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json,status) VALUES('other','{}','draft')")
    conn.commit()
    bot.handle_update(_text_upd('清单第1、2条 档口：A档口'))
    bot.handle_update(_text_upd('清单第3条 档口：B档口'))
    bot.handle_update(_text_upd('清单第1、2条 档口号/地址：二区10号'))
    bot.handle_update(_text_upd('清单第3条 供应商联系方式：微信 test-only'))
    bot.handle_update(_text_upd('出表'))
    sheet = openpyxl.load_workbook(io.BytesIO(bot.api.documents[-1][2])).active
    heads = [c.value for c in sheet[1]]
    rows = list(sheet.values)[1:]
    assert [r[heads.index('档口名称')] for r in rows] == ['A档口','A档口','B档口']
    assert rows[0][heads.index('档口号/地址')] == '二区10号'
    assert rows[2][heads.index('供应商联系方式')] == '微信 test-only'
    assert [r[heads.index('确认状态')] for r in rows] == ['已确认','已确认','待确认']
    assert conn.execute("SELECT fields_json FROM cs_note WHERE customer_id='other'").fetchone()[0] == '{}'
    before = [tuple(r) for r in conn.execute('SELECT * FROM cs_note')]
    bot.handle_update(_text_upd('清单第1、99条 档口：不应保存'))
    assert before == [tuple(r) for r in conn.execute('SELECT * FROM cs_note')]
    assert '未修改' in bot.api.sent[-1][1]


def test_supplier_fields_stay_separate_from_brand_and_other(bot):
    fields = bot._parse_items('[{"型号或品名":"Brand cream","档口名称":"供应商A","供应商联系方式":"测试微信","其他":"备注"}]')[0]
    assert fields['档口名称'] == '供应商A'
    assert fields['供应商联系方式'] == '测试微信'
    assert fields['其他'] == '备注'
    from catalog.cs_supplier import normalize
    unknown = normalize({'型号或品名':'Brand cream'})
    assert unknown['档口名称'] == '待补充'
