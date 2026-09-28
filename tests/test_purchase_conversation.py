"""Customer entrypoint -> persisted notes -> real XLSX; only network/model are replaced.

删C 后入口=H5 内核直调：send() 走 _text_turn/_on_photo；出表改走
/cs/link/{token}/export.xlsx（send 自动捕获导出文件，供 b.documents 断言）。
"""
import io
import json

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db, merchant_policy
from catalog.api import register_routes
from catalog.csbot import CsBot

_JPEG = None


def _jpeg_bytes():
    global _JPEG
    if _JPEG is None:
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(buf, format='JPEG')
        _JPEG = buf.getvalue()
    return _JPEG


def _export_bytes(conn, token):
    app = FastAPI()
    app.state.conn = conn
    app.state.token = 'purchase-test-service'
    app.state.storage = None
    app.state.callback = None
    register_routes(app)
    with TestClient(app) as client:
        response = client.get(f'/cs/link/{token}/export.xlsx')
    assert response.status_code == 200, response.text
    return response.content


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('CATALOG_CS_API_URL', raising=False)
    c = db.connect(str(tmp_path / 'shop.db')); db.init_db(c)
    c.execute("UPDATE shop_profile SET shop_name='测试店',tg_bot_id='123',owner_wechat='bosswx' WHERE id=1")
    merchant_policy.apply(c, {'shop_name':'测试店', 'logistics':'加急转人工'}, 1); c.commit()
    class Model:
        actions = []
        def chat_text(self, system, messages, **kw):
            if '采购记录抽取' in system:
                return json.dumps({'actions':self.actions}, ensure_ascii=False)
            if '规则匹配' in system:
                return 'TRANSFER' if '加急' in messages[-1]['content'] else 'PASS'
            return '{"action":"none"}'
        def chat_vision(self, *args, **kw):
            return '[{"型号或品名":"杯子","颜色":"白色"}]'
    model=Model(); bot=CsBot(c,None,llm=model,img_dir=str(tmp_path/'photos'))
    bot.sent=[]; bot.documents=[]
    from catalog import cs_i18n
    for tg in (100,200):
        cust=bot._ensure_customer({'id':tg})
        cs_i18n.set_language(c,cust['id'],'中文')
    c.commit()
    return c, bot, model


def send(bot, text='', uid=1, photo=False, customer=100):
    conn = bot.conn
    before = conn.execute('SELECT COUNT(*) FROM cs_link').fetchone()[0]
    cust = bot._ensure_customer({'id': customer})
    if photo:
        reply = bot._on_photo(cust, None, prepared=bot._prepare_photo(cust, _jpeg_bytes()))
    else:
        reply = bot._text_turn(cust, text)
    bot.sent.append(reply)
    after = conn.execute('SELECT COUNT(*) FROM cs_link').fetchone()[0]
    if after > before:
        token = conn.execute('SELECT token FROM cs_link WHERE customer_id=? ORDER BY rowid DESC',
                             (cust['id'],)).fetchone()[0]
        bot.documents.append((customer, '采购清单.xlsx', _export_bytes(conn, token)))
    return reply


def fields(c):
    return [json.loads(r[0]) for r in c.execute('SELECT fields_json FROM cs_note ORDER BY id')]


def exported_rows(workbook):
    result=[]
    for sheet in workbook:
        rows=list(sheet.values)
        index=next(i for i,row in enumerate(rows) if row[0]=='序号')
        result.extend(dict(zip(rows[index],row)) for row in rows[index+1:])
    return result


def test_text_creates_multiple_notes_and_exports(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','颜色':'蓝色','数量':'100个'}},
               {'op':'create','fields':{'型号或品名':'盘子','数量':'20件'}}]
    send(b,'帮我记：杯子蓝色100个，盘子20件')
    assert len(fields(c))==2
    m.actions=[]; send(b,'出表',2)
    assert len(b.documents)==1
    wb=openpyxl.load_workbook(io.BytesIO(b.documents[0][2]))
    rows=exported_rows(wb)
    assert len(rows)==2
    assert rows[0]['数量']=='100个' and rows[1]['数量']=='20件'


def test_records_quantity_even_when_question_transfers(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','颜色':'蓝色','数量':'100个'}}]
    send(b,'杯子蓝色100个，能加急吗')
    assert fields(c)[0]['数量']=='100个'
    assert 'bosswx' in b.sent[-1]
    assert '100个' in b.sent[-1]


def test_photo_then_text_merges_into_note_before_reply(setup):
    """图注通道已随 TG 拆除；照片后的文字轮完成同样的补写与转人工。"""
    c,b,m=setup
    send(b,photo=True)
    m.actions=[{'op':'update','index':1,'fields':{'颜色':'蓝色','数量':'100个'}}]
    send(b,'蓝色100个，能加急吗',2)
    assert len(fields(c))==1
    assert fields(c)[0]['数量']=='100个'
    assert fields(c)[0]['颜色']=='蓝色'
    assert 'bosswx' in b.sent[-1]


def test_replay_and_customer_isolation(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个'); send(b,'杯子100个')
    assert len(fields(c))==1
    m.actions=[{'op':'update','index':1,'fields':{'数量':'200个'}}]
    send(b,'改成200个',2,customer=200)
    assert fields(c)[0]['数量']=='100个'


def test_model_cannot_invent_quantity(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'999个'}}]
    send(b,'杯子有蓝色的吗')
    assert not fields(c)


def seed_product(c, tmp_path, monkeypatch):
    """动态分类测试商品：test_cat/p1（型号 C001，可观测，主图=IMG_DIR/sample.png）。"""
    from catalog import config, photo_inquiry
    from PIL import Image
    from tests.conftest import seed_products
    monkeypatch.setattr(config, 'IMG_DIR', str(tmp_path))
    Image.new('RGB', (20, 20), 'blue').save(tmp_path / 'sample.png')
    seed_products(c, [{'id': 'p1', 'inner_code': 'C001',
                       'data': {'model': 'C001'}, 'cs_visible': 1,
                       'images': ['sample.png']}])
    return photo_inquiry


def test_selected_photo_is_enriched_without_duplicate_and_excel_has_image(setup,tmp_path,monkeypatch):
    c,b,m=setup; inquiry=seed_product(c,tmp_path,monkeypatch)
    send(b,photo=True)
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'test_cat','product_id':'p1','name':'C001'}]);c.commit()
    m.actions=[];send(b,'选1',2)
    assert len(fields(c))==1
    assert fields(c)[0]['商品编号']=='p1'
    # Preserve the original customer photo; here replace its synthetic bytes with a real image.
    from PIL import Image
    photo=c.execute('SELECT photo FROM cs_note').fetchone()[0]
    Image.new('RGB',(20,20)).save(photo)
    send(b,'出表',3)
    wb=openpyxl.load_workbook(io.BytesIO(b.documents[0][2]))
    assert len(exported_rows(wb))==1 and sum(len(ws._images) for ws in wb)==1


def test_catalog_purchase_copies_product_image(setup,tmp_path,monkeypatch):
    c,b,m=setup;seed_product(c,tmp_path,monkeypatch)
    m.actions=[{'op':'create','fields':{'型号或品名':'C001','数量':'100个'}}]
    send(b,'我要C001 100个')
    assert fields(c)[0]['商品编号']=='p1'
    assert c.execute('SELECT photo FROM cs_note').fetchone()[0].endswith('sample.png')


def test_sheet_defined_product_can_be_attached_to_purchase_note(setup, tmp_path, monkeypatch):
    from catalog import config, dynamic_catalog
    from catalog.storage import LocalStorage
    c, b, model = setup
    monkeypatch.setattr(config, 'IMG_DIR', str(tmp_path))
    category = 'cat_hairdryer'
    dynamic_catalog.approve_template(c, {
        'key': category, 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    storage = LocalStorage(str(tmp_path)); image = storage.save(category, 'dryer', 'main.png', b'IMG')
    dynamic_catalog.upsert_approved_products(c, category, [{
        'id': 'dryer', 'inner_code': 'INNER-X', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '玫红色'}, 'images': [image],
    }]); c.commit()
    model.actions = [{'op': 'create', 'fields': {'型号或品名': 'HD15', '数量': '100个'}}]

    send(b, '我要HD15 100个')

    assert fields(c)[0]['商品编号'] == 'dryer'
    assert fields(c)[0]['颜色'] == '玫红色'


def test_hidden_selection_cannot_enter_notes(setup,tmp_path,monkeypatch):
    c,b,m=setup; inquiry=seed_product(c,tmp_path,monkeypatch)
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'test_cat','product_id':'p1','name':'C001'}])
    c.execute("UPDATE product_dynamic SET cs_visible=0");c.commit()
    send(b,'选1')
    assert fields(c)==[]
    assert '尚未加入清单' in b.sent[-1]


def test_record_and_export_in_same_message(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个，帮我出表')
    assert fields(c)[0]['数量']=='100个'
    assert b.documents


def test_record_with_price_request_does_not_export_or_reveal_quote_rules(setup):
    c,b,m=setup
    merchant_policy.apply(c, {'shop_name':'测试店', 'quote_rules':'整箱每件10元'}, 2)
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个，出表并报个底价')
    assert fields(c)[0]['数量']=='100个'
    assert b.documents
    assert 'bosswx' not in b.sent[-1]
    assert '10元' not in b.sent[-1]


def test_selection_with_many_drafts_requires_explicit_target(setup,tmp_path,monkeypatch):
    c,b,m=setup; inquiry=seed_product(c,tmp_path,monkeypatch)
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}},
               {'op':'create','fields':{'型号或品名':'盘子','数量':'20件'}}]
    send(b,'杯子100个，盘子20件')
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'test_cat','product_id':'p1','name':'C001'}]);c.commit()
    m.actions=[];send(b,'选1',2)
    assert all('商品编号' not in f for f in fields(c))
    assert '指定' in b.sent[-1]
    send(b,'选1 第2条',3)
    assert '商品编号' not in fields(c)[0]
    assert fields(c)[1]['商品编号']=='p1'


def test_catalog_list_without_purchase_keyword_is_linked(setup,tmp_path,monkeypatch):
    c,b,m=setup;seed_product(c,tmp_path,monkeypatch)
    from tests.conftest import seed_products as _seed
    _seed(c, [{'id': 'p2', 'inner_code': 'C002', 'data': {'model': 'C002'}}])
    c.commit()
    m.actions=[{'op':'create','fields':{'型号或品名':'C001','数量':'100个'}},
               {'op':'create','fields':{'型号或品名':'C002','数量':'20件'}}]
    send(b,'C001 100个，C002 20件')
    assert [f['商品编号'] for f in fields(c)]==['p1','p2']


def test_explicit_purchase_is_recorded_when_model_returns_no_actions(setup):
    c, bot, model = setup
    model.actions = []

    send(bot, '我想采购100台吹风机')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '已记录采购笔记' in bot.sent[-1]


def test_plain_single_product_and_quantity_is_recorded_when_model_misses(setup):
    c, bot, model = setup
    merchant_policy.apply(c, {'wechat_managed': True}, 2)
    c.commit()
    model.actions = []

    send(bot, '吹风机100台')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '暂不能确认' not in bot.sent[-1]
    send(bot, '出表', uid=2)
    workbook = openpyxl.load_workbook(io.BytesIO(bot.documents[-1][2]))
    assert workbook.active.max_row == 2
    assert '吹风机' in str(list(workbook.active.values))


def test_purchase_with_unanswered_question_keeps_note_receipt_and_answer_boundary(setup):
    c, bot, model = setup
    merchant_policy.apply(c, {'wechat_managed': True}, 2)
    c.commit()
    model.actions = []

    send(bot, '我想采购100台吹风机，多少钱？')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '已记录采购笔记' in bot.sent[-1]
    assert '暂不能确认' in bot.sent[-1]


def test_explicit_purchase_is_recorded_when_extraction_service_is_unavailable(setup, monkeypatch):
    c, bot, model = setup
    original = model.chat_text

    def unavailable(system, messages, **kwargs):
        if '采购记录抽取' in system:
            raise TimeoutError('model unavailable')
        return original(system, messages, **kwargs)

    monkeypatch.setattr(model, 'chat_text', unavailable)

    send(bot, '我想采购100台吹风机')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '已记录采购笔记' in bot.sent[-1]


@pytest.mark.parametrize('text', [
    '吹风机100台多少钱',
    '有没有100台吹风机',
    '如果采购100台吹风机呢',
    '吹风机100台有货吗',
    '100台吹风机可以今天发吗',
])
def test_questions_and_hypothetical_quantities_do_not_trigger_fallback_notes(setup, text):
    c, bot, model = setup
    model.actions = []

    send(bot, text)

    assert fields(c) == []


def test_explicit_purchase_uses_normalized_model_to_enrich_from_dynamic_catalog(setup, tmp_path, monkeypatch):
    from catalog import config, dynamic_catalog
    from PIL import Image

    c, bot, model = setup
    monkeypatch.setattr(config, 'IMG_DIR', str(tmp_path))
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
            {'key': 'power', 'label': '功率', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    image = tmp_path / 'cat_hairdryer' / 'dryer' / 'main.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (20, 20), 'silver').save(image)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [{
        'id': 'dryer', 'inner_code': 'INNER-X', 'cs_visible': 1,
        'data': {'model': 'WX-HD16', 'color': '银色', 'power': '1800W'},
        'images': ['cat_hairdryer/dryer/main.png'],
    }])
    c.commit()
    model.actions = []

    send(bot, '我想采购100台wx-hd16')

    note = fields(c)[0]
    assert note['型号或品名'] == 'WX-HD16'
    assert note['数量'] == '100台'
    assert note['颜色'] == '银色'
    assert note['功率'] == '1800W'
    assert note['商品编号'] == 'dryer'
    assert c.execute('SELECT photo FROM cs_note').fetchone()[0] == str(image)


def test_generic_category_purchase_does_not_guess_one_of_multiple_catalog_models(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [
        {'id': 'dryer-15', 'cs_visible': 1, 'data': {'model': 'WX-HD15', 'color': '红色'}},
        {'id': 'dryer-16', 'cs_visible': 1, 'data': {'model': 'WX-HD16', 'color': '银色'}},
    ])
    c.commit()
    model.actions = []

    send(bot, '我想采购100台吹风机')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]


def test_duplicate_catalog_models_require_more_specs_before_enrichment(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [
        {'id': 'dryer-red', 'cs_visible': 1, 'data': {'model': 'WX-HD15', 'color': '红色'}},
        {'id': 'dryer-blue', 'cs_visible': 1, 'data': {'model': 'WX-HD15', 'color': '蓝色'}},
    ])
    c.commit()
    model.actions = []

    send(bot, '我想采购100台WX-HD15')

    assert fields(c) == [{'型号或品名': 'WX-HD15', '数量': '100台'}]


def test_duplicate_model_query_lists_public_variants_without_guessing(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [
        {'id': 'dryer-red', 'cs_visible': 1, 'data': {'model': 'WX-HD15', 'color': '红色'}},
        {'id': 'dryer-blue', 'cs_visible': 1, 'data': {'model': 'WX-HD15', 'color': '蓝色'}},
    ])
    c.commit()
    model.actions = []

    send(bot, 'WX-HD15 有哪些颜色')

    reply = bot.sent[-1]
    assert '红色' in reply and '蓝色' in reply
    assert '多个' in reply and '暂不能确认' not in reply


def test_export_contains_catalog_enriched_and_unmatched_purchase_rows(setup, tmp_path, monkeypatch):
    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.voltage','220V') WHERE id='p1'")
    c.commit()
    model.actions = []

    send(bot, '我想采购100个c001')
    send(bot, '我想采购20套定制礼盒', uid=2)
    send(bot, '出表', uid=3)

    assert len(bot.documents) == 1
    workbook = openpyxl.load_workbook(io.BytesIO(bot.documents[0][2]))
    rows = exported_rows(workbook)
    assert len(rows) == 2
    assert sum(len(sheet._images) for sheet in workbook) == 1
    assert all('商品编号' not in row and '商品类别' not in row for row in rows)
    assert 'C001' in rows[0].values() and '220V' in rows[0].values() and '100个' in rows[0].values()
    assert '定制礼盒' in rows[1].values() and '20套' in rows[1].values()



def test_export_refreshes_catalog_fields_and_enriches_a_previously_unmatched_note(setup, tmp_path, monkeypatch):
    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.voltage','220V') WHERE id='p1'")
    c.commit()
    model.actions = []

    send(bot, '我想采购100个c001')
    send(bot, '我想采购20个future-1', uid=2)
    c.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.voltage','230V') WHERE id='p1'")
    from tests.conftest import seed_products as _seed
    _seed(c, [{'id': 'future', 'inner_code': 'FUTURE',
               'data': {'model': 'FUTURE-1', 'voltage': '110V'}}])
    c.commit()

    send(bot, '出表', uid=3)

    workbook = openpyxl.load_workbook(io.BytesIO(bot.documents[-1][2]))
    rows = [list(row.values()) for row in exported_rows(workbook)]
    assert any('C001' in row and '230V' in row and '220V' not in row for row in rows)
    assert any('FUTURE-1' in row and '110V' in row for row in rows)



def test_export_refresh_preserves_customer_edited_field(setup, tmp_path, monkeypatch):
    from catalog import shop_link

    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.voltage','220V') WHERE id='p1'")
    c.commit()
    model.actions = []
    send(bot, '我想采购100个c001')
    note = c.execute('SELECT * FROM cs_note').fetchone()
    shop_link.set_field(c, note, '电压', '客户确认240V')
    c.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.voltage','230V') WHERE id='p1'")
    c.commit()

    send(bot, '出表', uid=2)

    values = str(list(openpyxl.load_workbook(io.BytesIO(bot.documents[-1][2])).active.values))
    assert '客户确认240V' in values
    assert '230V' not in values


def test_catalog_binding_ids_are_not_sent_to_note_models(setup, tmp_path, monkeypatch):
    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    model.actions = []
    send(bot, '我想采购100个c001')
    prompts = []

    def capture(system, messages, **kwargs):
        prompts.append(json.dumps(messages, ensure_ascii=False))
        if '采购记录抽取' in system:
            return '{"actions":[]}'
        if '规则匹配' in system:
            return 'PASS'
        return '{"action":"none"}'

    model.chat_text = capture
    send(bot, '第一条数量改成200个', uid=2)

    assert prompts
    assert all('商品编号' not in prompt and '商品类别' not in prompt and 'p1' not in prompt
               for prompt in prompts)


def test_category_model_query_only_shows_products_from_that_category(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    from tests.conftest import seed_products as _seed
    _seed(c, [{'id': 'other', 'inner_code': 'C001', 'data': {'model': 'C001'}}], key='other_cat', name='其他品类')
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [{'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'}],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [
        {'id': 'dryer-15', 'cs_visible': 1, 'data': {'model': 'WX-HD15'}},
        {'id': 'dryer-16', 'cs_visible': 1, 'data': {'model': 'WX-HD16'}},
    ])
    c.commit()
    model.actions = []

    send(bot, '吹风机有哪些型号')

    reply = bot.sent[-1]
    assert 'WX-HD15' in reply and 'WX-HD16' in reply
    assert 'C001' not in reply


def test_plain_category_query_shows_that_category(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [{'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'}],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [{
        'id': 'dryer-15', 'cs_visible': 1, 'data': {'model': 'WX-HD15'},
    }])
    c.commit()
    model.actions = []

    send(bot, '查询吹风机')

    assert 'WX-HD15' in bot.sent[-1]


def test_stock_and_shipping_question_reports_missing_fields_without_promising(setup):
    from catalog import dynamic_catalog

    c, bot, model = setup
    dynamic_catalog.approve_template(c, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '产品型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(c, 'cat_hairdryer', [{
        'id': 'dryer-15', 'cs_visible': 1,
        'data': {'model': 'WX-HD15', 'color': '红色'},
    }])
    c.commit()
    model.actions = []

    send(bot, 'WX-HD15 有没有货？可以今天发吗？')

    reply = bot.sent[-1]
    assert 'WX-HD15' in reply
    assert '库存' in reply and ('尚未填写' in reply or '不能确认' in reply)
    assert '发货' in reply and ('尚未填写' in reply or '不能确认' in reply)
    assert '可以今天发' not in reply


def test_send_file_phrrasing_triggers_export(setup):
    """『直接发我文件』这类说法也必须触发导出（关键词快速通道），不能被大脑拒掉。"""
    from catalog import cs_i18n
    c, b, m = setup
    m.actions = [{'op': 'create', 'fields': {'型号或品名': '杯子', '数量': '10个'}}]
    send(b, '记一下杯子10个')
    send(b, '直接发我文件', 2)
    assert b.sent[-1].count('http') > 0 or '链接' in b.sent[-1]
    assert cs_i18n.wants_export('直接发我文件')


def test_brain_export_mark_sends_file_without_keywords(setup, monkeypatch):
    """措辞不含任何导出关键词：大脑自己判断意图，输出 <<EXPORT>> 也必须发文件。"""
    c, b, m = setup
    m.actions = [{'op': 'create', 'fields': {'型号或品名': '杯子', '数量': '10个'}}]
    send(b, '记一下杯子10个')
    original = m.chat_text
    def brain(system, messages, **kw):
        if '智能客服' in system:      # 对话大脑调用
            return '<<EXPORT>>'
        return original(system, messages, **kw)
    monkeypatch.setattr(m, 'chat_text', brain)
    send(b, '把我的单子弄成表格送过来', 2)   # 无任何触发词
    assert '清单' in b.sent[-1] and b.sent[-1].count('http') > 0
