import json
from catalog import photo_inquiry
from tests.test_release_gates import env, photo


def test_photo_candidate_requires_selection_and_uses_store_tier(env):
    conn,_,bot=env
    bot.llm.chat_vision.return_value=json.dumps([{'型号或品名':'MODEL-1','价格':'0.01','其他':'144含义待确认'}])
    bot.handle_update(photo(200))
    assert '询价1' in bot.api.send_message.call_args.args[1]
    assert '¥11' not in bot.api.send_message.call_args.args[1]
    customer=dict(conn.execute("SELECT * FROM cs_customer WHERE tg_id='100'").fetchone())
    reply=bot._on_text(customer,'询价1 60个')
    assert '老板' in reply and '¥11/个' not in reply and '0.01' not in reply
    assert conn.execute('SELECT COUNT(*) FROM product_curler').fetchone()[0]==1


def test_candidate_selection_rechecks_visibility_expiry_and_customer(env):
    conn,_,bot=env
    photo_inquiry.save(conn,'a',[{'category':'curler','product_id':'p1','name':'MODEL-1'}])
    conn.commit()
    assert '¥' not in bot._on_text({'id':'b'},'询价1 60个')
    conn.execute("UPDATE product_curler SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    assert '老板' in bot._on_text({'id':'a'},'询价1 60个')
    conn.execute("UPDATE cs_photo_candidates SET expires_at=datetime('now','-1 second')")
    conn.commit()
    assert '老板' in bot._on_text({'id':'a'},'询价1 60个')


def test_image_retrieval_candidates_never_expose_cost(env,monkeypatch):
    from catalog import config, search
    conn,_,bot=env
    conn.execute("INSERT INTO embedding(product_id,category,image_path,vec) VALUES('p1','curler','x',X'00000000')")
    conn.commit()
    monkeypatch.setattr(config,'BAILIAN_API_KEY','offline-stub')
    monkeypatch.setattr(search,'embed_image',lambda _: [1])
    monkeypatch.setattr(search,'query',lambda *a,**k:[{'category':'curler','product_id':'p1','score':0.91,'fields':{'价格':'7.35'}}])
    found=photo_inquiry.candidates(conn,[{'型号或品名':'sample'}],b'photo')
    assert found==[{'category':'curler','product_id':'p1','name':'MODEL-1'}]


def test_low_similarity_image_does_not_suggest_unrelated_product(env,monkeypatch):
    from catalog import config, search
    conn,_,_=env
    conn.execute("INSERT INTO embedding(product_id,category,image_path,vec) VALUES('p1','curler','x',X'00000000')")
    conn.commit()
    monkeypatch.setattr(config,'BAILIAN_API_KEY','offline-stub')
    monkeypatch.setattr(search,'embed_image',lambda _: [1])
    monkeypatch.setattr(search,'query',lambda *a,**k:[
        {'category':'curler','product_id':'p1','score':0.12}])
    assert photo_inquiry.local_candidates(conn,[{'型号或品名':'unrelated'}],b'photo')==[]


def test_sheet_defined_product_can_be_matched_and_selected_from_photo_text(env):
    from catalog import dynamic_catalog
    conn, _, _ = env
    dynamic_catalog.approve_template(conn, {
        'key': 'cat_hairdryer', 'name': '吹风机', 'source_sheet': '吹风机',
        'fields': [
            {'key': 'model', 'label': '型号', 'role': 'model', 'visibility': 'public'},
            {'key': 'color', 'label': '颜色', 'role': 'spec', 'visibility': 'public'},
        ],
    }, expected_version=0)
    dynamic_catalog.upsert_approved_products(conn, 'cat_hairdryer', [{
        'id': 'dryer-1', 'inner_code': 'INNER-X', 'cs_visible': 1,
        'data': {'model': 'HD15', 'color': '玫红色'},
    }])
    conn.commit()

    found = photo_inquiry.local_candidates(conn, [{'型号或品名': '戴森 HD15 吹风机'}], b'photo')
    assert found == [{'category': 'cat_hairdryer', 'product_id': 'dryer-1', 'name': 'HD15'}]
    photo_inquiry.save(conn, 'a', found); conn.commit()
    selected = photo_inquiry.selection(conn, 'a', 1)
    assert selected['id'] == 'dryer-1' and selected['specs']['颜色'] == '玫红色'
