"""Customer entrypoint -> persisted notes -> real XLSX; only network/model are replaced."""
import io
import json
from types import SimpleNamespace

import openpyxl
import pytest
from catalog import db, merchant_policy
from catalog.csbot import CsBot


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
    api = SimpleNamespace(sent=[], documents=[], download_photo=lambda p:b'fake')
    api.send_message=lambda *a:api.sent.append(a)
    api.send_document=lambda *a:api.documents.append(a)
    model=Model(); bot=CsBot(c,api,llm=model,img_dir=str(tmp_path/'photos'))
    return c, bot, model


def send(bot, text='', uid=1, photo=False, customer=100):
    msg={'from':{'id':customer}, 'chat':{'id':customer,'type':'private'}}
    msg.update({'photo':[{'file_id':'x'}], 'caption':text} if photo else {'text':text})
    bot.handle_update({'update_id':uid,'message':msg})


def fields(c):
    return [json.loads(r[0]) for r in c.execute('SELECT fields_json FROM cs_note ORDER BY id')]


def test_text_creates_multiple_notes_and_exports(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','颜色':'蓝色','数量':'100个'}},
               {'op':'create','fields':{'型号或品名':'盘子','数量':'20件'}}]
    send(b,'帮我记：杯子蓝色100个，盘子20件')
    assert len(fields(c))==2
    m.actions=[]; send(b,'出表',2)
    assert len(b.api.documents)==1
    wb=openpyxl.load_workbook(io.BytesIO(b.api.documents[0][2]))
    assert wb.active.max_row==3
    assert '100个' in str(list(wb.active.values))


def test_records_quantity_even_when_question_transfers(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','颜色':'蓝色','数量':'100个'}}]
    send(b,'杯子蓝色100个，能加急吗')
    assert fields(c)[0]['数量']=='100个'
    assert 'bosswx' in b.api.sent[-1][1]
    assert '100个' in b.api.sent[-1][1]


def test_caption_merges_into_photo_note_before_reply(setup):
    c,b,m=setup
    m.actions=[{'op':'update','index':1,'fields':{'颜色':'蓝色','数量':'100个'}}]
    send(b,'蓝色100个，能加急吗',photo=True)
    assert len(fields(c))==1
    assert fields(c)[0]['数量']=='100个'
    assert fields(c)[0]['颜色']=='蓝色'
    assert 'bosswx' in b.api.sent[-1][1]


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
    from catalog import config, photo_inquiry
    from PIL import Image
    monkeypatch.setattr(config,'IMG_DIR',str(tmp_path))
    Image.new('RGB',(20,20),'blue').save(tmp_path/'sample.png')
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,status,cs_visible,image_main) VALUES('p1','C001','C001','approved',1,'sample.png')")
    cust=CsBot._ensure_customer
    return photo_inquiry


def test_selected_photo_is_enriched_without_duplicate_and_excel_has_image(setup,tmp_path,monkeypatch):
    c,b,m=setup; inquiry=seed_product(c,tmp_path,monkeypatch)
    send(b,photo=True)
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}]);c.commit()
    m.actions=[];send(b,'选1',2)
    assert len(fields(c))==1
    assert fields(c)[0]['商品编号']=='p1'
    # Preserve the original customer photo; here replace its synthetic bytes with a real image.
    from PIL import Image
    photo=c.execute('SELECT photo FROM cs_note').fetchone()[0]
    Image.new('RGB',(20,20)).save(photo)
    send(b,'出表',3)
    wb=openpyxl.load_workbook(io.BytesIO(b.api.documents[0][2]))
    assert wb.active.max_row==2 and len(wb.active._images)==1


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
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}])
    c.execute("UPDATE product_curler SET cs_visible=0");c.commit()
    send(b,'选1')
    assert fields(c)==[]
    assert '尚未加入清单' in b.api.sent[-1][1]


def test_record_and_export_in_same_message(setup):
    c,b,m=setup
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个，帮我出表')
    assert fields(c)[0]['数量']=='100个'
    assert b.api.documents


def test_record_with_price_request_does_not_export_or_reveal_quote_rules(setup):
    c,b,m=setup
    merchant_policy.apply(c, {'shop_name':'测试店', 'quote_rules':'整箱每件10元'}, 2)
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}}]
    send(b,'杯子100个，出表并报个底价')
    assert fields(c)[0]['数量']=='100个'
    assert b.api.documents
    assert 'bosswx' not in b.api.sent[-1][1]
    assert '10元' not in b.api.sent[-1][1]


def test_selection_with_many_drafts_requires_explicit_target(setup,tmp_path,monkeypatch):
    c,b,m=setup; inquiry=seed_product(c,tmp_path,monkeypatch)
    m.actions=[{'op':'create','fields':{'型号或品名':'杯子','数量':'100个'}},
               {'op':'create','fields':{'型号或品名':'盘子','数量':'20件'}}]
    send(b,'杯子100个，盘子20件')
    cust=b._ensure_customer({'id':100})
    inquiry.save(c,cust['id'],[{'category':'curler','product_id':'p1','name':'C001'}]);c.commit()
    m.actions=[];send(b,'选1',2)
    assert all('商品编号' not in f for f in fields(c))
    assert '指定' in b.api.sent[-1][1]
    send(b,'选1 第2条',3)
    assert '商品编号' not in fields(c)[0]
    assert fields(c)[1]['商品编号']=='p1'


def test_catalog_list_without_purchase_keyword_is_linked(setup,tmp_path,monkeypatch):
    c,b,m=setup;seed_product(c,tmp_path,monkeypatch)
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,status,cs_visible) VALUES('p2','C002','C002','approved',1)")
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
    assert '已记录采购笔记' in bot.api.sent[-1][1]


def test_plain_single_product_and_quantity_is_recorded_when_model_misses(setup):
    c, bot, model = setup
    merchant_policy.apply(c, {'wechat_managed': True}, 2)
    c.commit()
    model.actions = []

    send(bot, '吹风机100台')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '暂不能确认' not in bot.api.sent[-1][1]
    send(bot, '出表', uid=2)
    workbook = openpyxl.load_workbook(io.BytesIO(bot.api.documents[-1][2]))
    assert workbook.active.max_row == 2
    assert '吹风机' in str(list(workbook.active.values))


def test_purchase_with_unanswered_question_keeps_note_receipt_and_answer_boundary(setup):
    c, bot, model = setup
    merchant_policy.apply(c, {'wechat_managed': True}, 2)
    c.commit()
    model.actions = []

    send(bot, '我想采购100台吹风机，多少钱？')

    assert fields(c) == [{'型号或品名': '吹风机', '数量': '100台'}]
    assert '已记录采购笔记' in bot.api.sent[-1][1]
    assert '暂不能确认' in bot.api.sent[-1][1]


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
    assert '已记录采购笔记' in bot.api.sent[-1][1]


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

    reply = bot.api.sent[-1][1]
    assert '红色' in reply and '蓝色' in reply
    assert '多个' in reply and '暂不能确认' not in reply


def test_export_contains_catalog_enriched_and_unmatched_purchase_rows(setup, tmp_path, monkeypatch):
    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_curler SET voltage='220V' WHERE id='p1'")
    c.commit()
    model.actions = []

    send(bot, '我想采购100个c001')
    send(bot, '我想采购20套定制礼盒', uid=2)
    send(bot, '出表', uid=3)

    assert len(bot.api.documents) == 1
    workbook = openpyxl.load_workbook(io.BytesIO(bot.api.documents[0][2]))
    sheet = workbook.active
    rows = list(sheet.values)
    headers = list(rows[0])
    assert sheet.max_row == 3
    assert len(sheet._images) == 1
    assert '商品编号' not in headers and '商品类别' not in headers
    assert 'C001' in rows[1] and '220V' in rows[1] and '100个' in rows[1]
    assert '定制礼盒' in rows[2] and '20套' in rows[2]


def test_export_refreshes_catalog_fields_and_enriches_a_previously_unmatched_note(setup, tmp_path, monkeypatch):
    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_curler SET voltage='220V' WHERE id='p1'")
    c.commit()
    model.actions = []

    send(bot, '我想采购100个c001')
    send(bot, '我想采购20个future-1', uid=2)
    c.execute("UPDATE product_curler SET voltage='230V' WHERE id='p1'")
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,voltage,status,cs_visible) "
              "VALUES('future','FUTURE','FUTURE-1','110V','approved',1)")
    c.commit()

    send(bot, '出表', uid=3)

    sheet = openpyxl.load_workbook(io.BytesIO(bot.api.documents[-1][2])).active
    rows = list(sheet.values)
    assert any('C001' in row and '230V' in row and '220V' not in row for row in rows[1:])
    assert any('FUTURE-1' in row and '110V' in row for row in rows[1:])


def test_export_refresh_preserves_customer_edited_field(setup, tmp_path, monkeypatch):
    from catalog import shop_link

    c, bot, model = setup
    seed_product(c, tmp_path, monkeypatch)
    c.execute("UPDATE product_curler SET voltage='220V' WHERE id='p1'")
    c.commit()
    model.actions = []
    send(bot, '我想采购100个c001')
    note = c.execute('SELECT * FROM cs_note').fetchone()
    shop_link.set_field(c, note, '电压', '客户确认240V')
    c.execute("UPDATE product_curler SET voltage='230V' WHERE id='p1'")
    c.commit()

    send(bot, '出表', uid=2)

    values = str(list(openpyxl.load_workbook(io.BytesIO(bot.api.documents[-1][2])).active.values))
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
    c.execute("INSERT INTO product_curler(id,inner_code,item_no,status,cs_visible) VALUES('other','C001','C001','approved',1)")
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

    reply = bot.api.sent[-1][1]
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

    assert 'WX-HD15' in bot.api.sent[-1][1]


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

    reply = bot.api.sent[-1][1]
    assert 'WX-HD15' in reply
    assert '库存' in reply and ('尚未填写' in reply or '不能确认' in reply)
    assert '发货' in reply and ('尚未填写' in reply or '不能确认' in reply)
    assert '可以今天发' not in reply
