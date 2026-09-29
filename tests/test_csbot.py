"""C端客服内核：照片→抽取回执→确认→出表链接；文本→persona（红线知识注入判定）。

删C 后传输层已拆：用例直调内核（_on_text/_on_photo/_make_link），
语言用 cs_i18n.set_language 预置，出表文件走 /cs/link/{token}/export.xlsx。
"""
import io
import json
import sqlite3
import tempfile

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from catalog import cs, db
from catalog.api import register_routes
from catalog.csbot import CsBot, TRANSFER_MARK


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


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
        if '草稿编辑' in system:
            return self.edit_reply
        return self.text_reply

    def chat_vision(self, prompt, image_bytes, **kw):
        self.calls.append(('vision', prompt))
        return self.vision_reply


@pytest.fixture()
def bot(conn):
    llm = FakeLlm()
    b = CsBot(conn, None, llm=llm)
    # H5 路由同款预置：客户 tg_id 固定 100，语言预置“中文”，用例只关心业务行为。
    from catalog import cs_i18n
    b._ensure_customer({'id': 100, 'username': 'buyer'})
    cs_i18n.set_language(
        conn, conn.execute("SELECT id FROM cs_customer WHERE tg_id='100'").fetchone()[0], '中文')
    conn.commit()
    return b


@pytest.fixture()
def cust(bot, conn):
    return conn.execute("SELECT * FROM cs_customer WHERE tg_id='100'").fetchone()


def _photo(bot, cust, data=b'fake-jpeg-bytes'):
    """H5 同款照片链路：_prepare_photo + _on_photo，返回回执。"""
    prepared = bot._prepare_photo(cust, data)
    return bot._on_photo(cust, None, prepared=prepared)


def _export(conn, bot, cust, workbook=False):
    """出表：_make_link 生成 cs_link，再走页面导出接口拿 Excel。"""
    reply = bot._make_link(cust)
    row = conn.execute('SELECT token FROM cs_link WHERE customer_id=?',
                       (cust['id'],)).fetchone()
    if row is None:
        return reply, None
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'test-service'
    app.state.storage = None
    app.state.callback = None
    register_routes(app)
    with TestClient(app) as client:
        response = client.get(f"/cs/link/{row['token']}/export.xlsx")
    assert response.status_code == 200
    result = openpyxl.load_workbook(io.BytesIO(response.content))
    return reply, result if workbook else result.active


# ---------- 语言候选（删A：只留中文/English，检测与翻译机制保留） ----------

def test_language_candidates_cover_all_thirteen_with_legacy_aliases():
    from catalog import cs_i18n
    for code,name in cs_i18n.LANGUAGES.items():
        assert cs_i18n.detect_language(code)==code
        assert cs_i18n.detect_language(name)==code
    assert cs_i18n.parse_language_request('我想转英文') == ('switch','en')
    assert cs_i18n.detect_language('我想转英文') is None
    assert cs_i18n.LANGUAGE_PROMPT.count('·')==12


# ---------- 拍照整理 ----------

def test_photo_creates_draft_and_receipt(bot, conn, cust):
    receipt = _photo(bot, cust)
    notes = conn.execute("SELECT * FROM cs_note WHERE status='draft'").fetchall()
    assert len(notes) == 1
    assert json.loads(notes[0]['fields_json'])['价格'] == '80R（照片识别，待确认）'
    assert notes[0]['photo']                          # 图已落盘路径
    assert '直发夹板' in receipt and '80R' in receipt
    assert '没拍到' in receipt                         # 没抽到的字段明说


def test_confirm_promotes_drafts(bot, conn, cust):
    _photo(bot, cust)
    reply = bot._on_text(cust, '确认')
    assert conn.execute("SELECT COUNT(*) c FROM cs_note WHERE status='confirmed'").fetchone()['c'] == 1
    assert '1 条' in reply


def test_export_link(bot, conn, cust):
    _photo(bot, cust)
    bot._on_text(cust, '确认')
    reply = bot._on_text(cust, '出表')
    row = conn.execute('SELECT * FROM cs_link').fetchone()
    assert row and row['customer_id']
    assert row['token'] in reply                       # 链接含一次性 token


# ---------- 草稿自然语言补改 ----------

def test_draft_edit_applies_and_rereceipts(bot, conn, cust):
    _photo(bot, cust)
    before = conn.execute('SELECT fields_json FROM cs_note').fetchone()[0]
    bot.llm.edit_reply = '{"action":"edit","index":1,"field":"价格","value":"2.5"}'
    reply = bot._on_text(cust, '1 价格改成2.5')
    assert conn.execute('SELECT fields_json FROM cs_note').fetchone()[0] != before
    assert '老板' not in reply


def test_draft_add_field_to_last(bot, conn, cust):
    _photo(bot, cust)
    bot.llm.edit_reply = '{"action":"add","field":"起订量","value":"1箱"}'
    bot._on_text(cust, '起订量一箱起')
    f = json.loads(conn.execute("SELECT fields_json FROM cs_note WHERE status='draft'")
                   .fetchone()['fields_json'])
    assert f['起订量'] == '1箱'


def test_draft_delete_note(bot, conn, cust):
    _photo(bot, cust)
    bot.llm.edit_reply = '{"action":"delete_note","index":1}'
    bot._on_text(cust, '第1条删了')
    n = conn.execute("SELECT COUNT(*) c FROM cs_note WHERE status='draft'").fetchone()['c']
    assert n == 0


def test_non_edit_message_falls_to_persona(bot, conn, cust):
    _photo(bot, cust)
    bot.llm.edit_reply = '{"action":"none"}'
    assert bot._on_text(cust, '你们还有什么货？') == '您好，可以告诉我商品型号和采购数量，或发照片整理清单。'  # 落回 persona


def test_catalog_brief_carries_cost_price(bot, conn):
    """底价红线可判：商品简表带出厂价（内部价不外报）。"""
    from tests.conftest import seed_products
    seed_products(conn, [{'id': 'p1', 'inner_code': 'KS-X',
                          'data': {'model': '8226', 'price': '10'}, 'cs_visible': 1}])
    brief = bot._catalog_brief()
    assert '8226' in brief and '成本价' not in brief and '10' not in brief


def test_catalog_query_sends_only_three_spec_cards_without_link_or_price(bot, conn, cust):
    from tests.conftest import seed_products
    seed_products(conn, [
        {'id': f'p{index}', 'inner_code': f'KS-{index}',
         'data': {'model': f'MODEL-{index}', 'price': f'{99 + index}.99',
                  'voltage': '220V', 'color': '黑色'}, 'cs_visible': 1}
        for index in range(4)])

    reply = bot._on_text(cust, '有哪些商品')

    assert all(value not in reply for value in ('http://', 'https://', '价格', '报价', '99.99'))
    assert reply.count('MODEL-') == 3
    assert '220V' in reply and '黑色' in reply


def test_dynamic_product_query_excludes_cost_and_price(bot, conn, cust):
    from catalog import dynamic_catalog
    from catalog import merchant_policy
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
    dynamic_catalog.upsert_approved_products(conn, category, [{
        'id': 'dryer', 'inner_code': 'INNER-1', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '玫红色', 'cost': '35.00', 'price': '88.00'},
    }])
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    cs.set_redline(conn, None, '')
    conn.commit()

    reply = bot._on_text(cust, 'HD15 有什么规格')

    assert 'HD15' in reply and '玫红色' in reply
    assert all(value not in reply for value in ('35.00', '88.00', '成本', '价格', 'http'))


def test_managed_photo_selection_returns_selected_public_product_details(bot, conn, cust):
    from catalog import dynamic_catalog, merchant_policy, photo_inquiry
    dynamic_catalog.approve_template(conn, {
        'key': 'photo_selection', 'name': '电器', 'source_sheet': '电器',
        'fields': [
            {'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'material', 'label': '材质', 'role': 'spec', 'visibility': 'public'},
            {'key': 'price', 'label': '价格', 'role': 'price', 'visibility': 'internal'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(conn, 'photo_selection', [{
        'id': 'picked', 'inner_code': 'INTERNAL-1', 'cs_visible': 1,
        'data': {'model': 'TEST-8226', 'material': 'PBT', 'price': '21.5'},
    }])
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    photo_inquiry.save(conn, cust['id'], [
        {'category': 'photo_selection', 'product_id': 'picked', 'name': 'TEST-8226'}])
    conn.commit()

    reply = bot._on_text(cust, '询价1')

    assert 'TEST-8226' in reply and 'PBT' in reply
    assert '21.5' not in reply and '价格' not in reply


def test_managed_photo_next_batch_keeps_selection_numbers(bot, conn, cust):
    from catalog import dynamic_catalog, merchant_policy, photo_inquiry
    dynamic_catalog.approve_template(conn, {
        'key': 'page_test', 'name': '分页商品', 'source_sheet': '分页商品',
        'fields': [{'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'}],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(conn, 'page_test', [
        {'id': f'p{i}', 'inner_code': f'I{i}', 'cs_visible': 1,
         'data': {'model': f'M{i}'}} for i in range(1, 6)])
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    photo_inquiry.save(conn, cust['id'], [
        {'category': 'page_test', 'product_id': f'p{i}', 'name': f'M{i}'}
        for i in range(1, 6)])
    conn.commit()

    reply = bot._on_text(cust, '换一批')

    assert '询价1：M4' in reply and '询价2：M5' in reply
    assert 'M4' in bot._on_text(cust, '询价1')
    assert '暂无更多' in bot._on_text(cust, '换一批')


def test_managed_photo_match_does_not_promise_an_unconfigured_handoff(bot, conn, tmp_path):
    from catalog import merchant_policy
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    cs.set_redline(conn, None, '')
    photo_path = tmp_path / 'customer.jpg'
    photo_path.write_bytes(b'photo')

    reply = bot._on_photo(
        {'id': 'buyer'},
        None,
        prepared=(str(photo_path), [{'型号或品名': 'MODEL-1'}], [
            {'category': 'test_cat', 'product_id': 'p1', 'name': 'MODEL-1'},
        ], []),
    )

    assert '回复“询价1”即可转人工' not in reply
    assert '回复“询价1”查看对应商品资料' in reply


# ---------- persona + 红线 ----------

def test_persona_reply_and_knowledge_injection(bot, conn, cust):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    reply = bot._on_text(cust, '你好，有什么货？')
    kind, system, messages = bot.llm.calls[-1]
    assert kind == 'text'
    assert '数量少于50转人工' in system                 # 红线知识注入 system prompt
    assert '军火' not in system
    assert reply == '您好，可以告诉我商品型号和采购数量，或发照片整理清单。'  # 正常回复透传


def test_transfer_escalates_with_rule_citation(bot, conn, cust):
    cs.set_redline(conn, None, '数量少于50的转人工', '数量少于50转人工')
    bot.llm.text_reply = f'{TRANSFER_MARK}数量少于50转人工'
    reply = bot._on_text(cust, '30个多少钱')
    # 客户侧：顶话术，不硬答
    assert '老板' in reply
    # 商家侧：微信提醒含客户原话+红线原文（内核只入队，投递统一走 notify.deliver）
    from catalog import notify
    notified = []
    notify.deliver(conn, notifier=notified.append, file_sender=lambda body: None)
    assert len(notified) == 1
    remind = notified[0]
    assert '30个多少钱' in remind and '数量少于50的转人工' in remind


def test_conversation_logged(bot, conn, cust):
    bot._on_text(cust, '在吗')
    rows = conn.execute("SELECT role FROM cs_conversation_log ORDER BY id").fetchall()
    # 内核直调只记 assistant 轮（H5 现行为：user 轮不入库）。
    assert [r['role'] for r in rows] == ['assistant']


def test_customer_upserted(bot, conn):
    bot._ensure_customer({'id': '200', 'username': 'guest'})
    row = conn.execute('SELECT * FROM cs_customer WHERE tg_id=?', ('200',)).fetchone()
    assert row['tg_name'] == 'guest'


def test_export_page_has_photos_and_customer_isolation(bot, conn, cust, tmp_path):
    from PIL import Image
    bot.img_dir = str(tmp_path)
    _photo(bot, cust, data=_jpeg_bytes())
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('other','999')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json,status) VALUES('other','{\"私密\":\"其他客户\"}','confirmed')")
    conn.commit()
    reply, sheet = _export(conn, bot, cust)
    assert '1 条待确认' in reply and sheet is not None
    assert sheet.max_row == 2 and len(sheet._images) == 1
    assert '待确认' not in [c.value for c in sheet[2]]   # 确认状态列已按需求移除
    assert '其他客户' not in str(list(sheet.values))
    assert conn.execute("SELECT status FROM cs_note WHERE customer_id!='other'").fetchone()[0] == 'draft'


def test_export_empty_list_does_not_send_empty_excel(bot, conn, cust):
    reply = bot._on_text(cust, '出表')
    assert '没有可导出' in reply
    assert conn.execute('SELECT COUNT(*) FROM cs_link').fetchone()[0] == 0


def test_assign_different_suppliers_and_export_without_cross_customer_changes(bot, conn, cust):
    _photo(bot, cust)
    _photo(bot, cust)
    bot._on_text(cust, '确认')
    _photo(bot, cust)
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('other','999')")
    conn.execute("INSERT INTO cs_note(customer_id,fields_json,status) VALUES('other','{}','draft')")
    conn.commit()
    bot._on_text(cust, '清单第1、2条 档口：A档口')
    bot._on_text(cust, '清单第3条 档口：B档口')
    bot._on_text(cust, '清单第1、2条 档口号/地址：二区10号')
    bot._on_text(cust, '清单第3条 供应商联系方式：微信 test-only')
    reply, workbook = _export(conn, bot, cust, workbook=True)
    assert '清单' in reply
    rows = []
    for sheet in workbook:
        values = list(sheet.values)
        header_index = next(i for i,row in enumerate(values) if row[0]=='序号')
        heads = list(values[header_index])
        rows.extend(dict(zip(heads,row)) for row in values[header_index+1:])
        assert '确认状态' not in heads and '起订量' not in heads
    assert [r['档口名称'] for r in rows] == ['A档口','A档口','B档口']
    assert rows[0]['档口号/地址'] == '二区10号'
    assert rows[2]['供应商联系方式'] == '微信 test-only'
    assert conn.execute("SELECT fields_json FROM cs_note WHERE customer_id='other'").fetchone()[0] == '{}'
    before = [tuple(r) for r in conn.execute('SELECT * FROM cs_note')]
    reply = bot._on_text(cust, '清单第1、99条 档口：不应保存')
    assert before == [tuple(r) for r in conn.execute('SELECT * FROM cs_note')]
    assert '未修改' in reply


def test_supplier_fields_stay_separate_from_brand_and_other(bot):
    fields = bot._parse_items('[{"型号或品名":"Brand cream","档口名称":"供应商A","供应商联系方式":"测试微信","其他":"备注"}]')[0]
    assert fields['档口名称'] == '供应商A'
    assert fields['供应商联系方式'] == '测试微信'
    assert fields['其他'] == '备注'
    from catalog.cs_supplier import normalize
    unknown = normalize({'型号或品名': 'Brand cream'})
    assert unknown['档口名称'] == '待补充'


def test_business_card_photo_sets_shop_info_not_note(bot, conn, cust):
    """名片：不生成商品笔记，档口信息入 cs_card_info 并覆盖导出档口列。"""
    bot.llm.vision_reply = json.dumps([
        {'名片': {'档口名称': '宏发电器', '供应商联系人': '王宏',
                 '供应商联系方式': 'wx-123', '档口号/地址': 'F区21号'}}], ensure_ascii=False)
    receipt = _photo(bot, cust)
    assert conn.execute("SELECT COUNT(*) FROM cs_note").fetchone()[0] == 0
    card = conn.execute("SELECT fields_json FROM note_batches WHERE state='pending'").fetchone()
    assert json.loads(card[0])['档口名称'] == '宏发电器'
    assert '宏发电器' in receipt


def test_multi_product_photo_crops_subimages(bot, conn, cust, tmp_path):
    """一图多商品：图框裁出子图分别挂笔记；无框回落整图。"""
    bot.img_dir = str(tmp_path)
    bot.llm.vision_reply = json.dumps([
        {'型号或品名': 'A款', '颜色': '红', '图框': [0, 0, 500, 1000]},
        {'型号或品名': 'B款', '颜色': '蓝', '图框': [500, 0, 1000, 1000]},
        {'型号或品名': 'C款', '颜色': '绿'}], ensure_ascii=False)
    _photo(bot, cust, data=_jpeg_bytes())
    photos = [r[0] for r in conn.execute('SELECT photo FROM cs_note ORDER BY id')]
    assert len(photos) == 3
    assert photos[0].endswith('_crop0.jpg') and photos[1].endswith('_crop1.jpg')
    assert photos[2] == photos[2] and not photos[2].endswith('_crop2.jpg')   # 无框=整图
    from PIL import Image as I2
    assert I2.open(photos[0]).size == (500, 500)


def _jpeg_bytes():
    buf = io.BytesIO()
    from PIL import Image
    Image.new('RGB', (1000, 500), 'white').save(buf, format='JPEG')
    return buf.getvalue()
