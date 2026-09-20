"""Additional offline boundary checks; correct business behavior is asserted."""
import io
import json
from unittest.mock import Mock

import openpyxl
import pytest

from tests.test_release_gates import env, auth, photo
from catalog import cs, tickets


@pytest.mark.parametrize('tiers', ['20:12;20:11', '-20:12', '0.5:12', '20:12;50:-11'])
def test_invalid_tier_table_not_accepted(env, tiers):
    _, client, _ = env
    response = client.patch('/products/curler/p1', headers=auth(),
                            json={'changes': {'可观测': '1', '阶梯价': tiers}})
    assert response.status_code == 400, f'invalid or partial tier table accepted: {tiers}'


def test_boolean_visibility_really_makes_product_visible(env):
    conn, client, bot = env
    response = client.patch('/products/curler/p1', headers=auth(),
                            json={'changes': {'可观测': True}})
    assert response.status_code == 200
    ticket = response.json()
    response = client.post(f'/tickets/{ticket["ticket_id"]}/decision',
                           json={'token': ticket['token'], 'approved': True})
    assert response.status_code == 200
    assert 'MODEL-1' in bot._catalog_brief(), 'accepted true value silently removes product from visible catalog'


def test_customer_export_treats_user_text_as_literal(env):
    conn, client, _ = env
    nid = conn.execute("SELECT id FROM cs_note WHERE customer_id='a'").fetchone()[0]
    client.patch(f'/cs/link/link-a/note/{nid}', json={'field': '备注', 'value': '=1+1'})
    response = client.get('/cs/link/link-a/export.xlsx')
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    cells = [c for row in wb.active for c in row]
    assert all(c.data_type != 'f' for c in cells), 'user-controlled note exported as executable Excel formula'


@pytest.mark.parametrize('bad_json', ['[]', 'null', '{"action":"edit","index":"第一个","field":"价格","value":"5"}'])
def test_bad_model_edit_output_has_safe_fallback(env, bad_json):
    _, _, bot = env
    bot.handle_update(photo(1))
    bot.llm.chat_text.return_value = bad_json
    update = {'update_id': 2, 'message': {'chat': {'id': 100}, 'from': {'id': 100}, 'text': '第一个改价'}}
    bot.handle_update(update)
    assert bot.api.send_message.call_count == 2


def test_same_customer_text_not_duplicated_in_llm_history(env):
    _, _, bot = env
    bot.handle_update({'update_id': 9, 'message': {'chat': {'id': 100},
                      'from': {'id': 100}, 'text': 'MODEL-1 介绍一下'}})
    messages = bot.llm.chat_text.call_args.args[1]
    assert sum(m['content'] == 'MODEL-1 介绍一下' for m in messages) == 1


def test_bot_two_buyers_photo_confirmation_stays_separate(env):
    conn, _, bot = env
    bot.handle_update(photo(1))
    other = photo(2)
    other['message']['from']['id'] = 200
    other['message']['chat']['id'] = 200
    bot.handle_update(other)
    bot.handle_update({'update_id': 3, 'message': {'chat': {'id': 100}, 'from': {'id': 100}, 'text': '确认'}})
    rows = conn.execute("SELECT c.tg_id,n.status FROM cs_note n JOIN cs_customer c ON c.id=n.customer_id WHERE c.tg_id IN ('100','200') ORDER BY c.tg_id").fetchall()
    assert [tuple(r) for r in rows] == [('100', 'confirmed'), ('200', 'draft')]


def test_customer_photo_does_not_write_merchant_catalog(env):
    conn, _, bot = env
    before = conn.execute('SELECT COUNT(*) FROM product_curler').fetchone()[0]
    bot.handle_update(photo())
    bot.handle_update({'message': {'chat': {'id': 100}, 'from': {'id': 100}, 'text': '确认'}})
    assert conn.execute('SELECT COUNT(*) FROM product_curler').fetchone()[0] == before
    assert conn.execute('SELECT COUNT(*) FROM product_razor').fetchone()[0] == 0


def test_hidden_and_delisted_products_not_in_brief(env):
    conn, _, bot = env
    conn.execute("UPDATE product_curler SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    assert 'MODEL-1' not in bot._catalog_brief()
    conn.execute("UPDATE product_curler SET cs_visible=1,status='delisted' WHERE id='p1'")
    conn.commit()
    assert 'MODEL-1' not in bot._catalog_brief()


def test_row_approval_retry_does_not_duplicate_product(env):
    conn, client, _ = env
    tk = tickets.create(conn, 'import', 'razor', {'kind': 'import', 'work_dir': None,
        'drafts': {'new': [{'model_no': 'R-A', '_rid': 'n0'}, {'model_no': 'R-B', '_rid': 'n1'}],
                   'update': [], 'delist': []}})
    body = {'token': tk['token'], 'row_key': 'n0', 'approved': True}
    first = client.post(f'/tickets/{tk["id"]}/row', json=body)
    assert first.status_code == 200
    client.post(f'/tickets/{tk["id"]}/row', json=body)
    assert conn.execute("SELECT COUNT(*) FROM product_razor WHERE model_no='R-A'").fetchone()[0] == 1


def test_import_preview_does_not_disclose_service_token(env):
    conn, client, _ = env
    tk = tickets.create(conn, 'import', 'razor', {'kind': 'import', 'work_dir': '/tmp/audit-only',
        'drafts': {'new': [{'model_no': 'P', 'image_main': 'a.jpg', '_rid': 'n0'}],
                   'update': [], 'delist': []}})
    response = client.get(f'/tickets/{tk["id"]}')
    assert 'audit-service-secret' not in response.text, 'anonymous preview reveals service-wide write credential'


def test_import_keeps_separate_rows_with_same_model():
    from catalog.classify import classify
    parsed = [{'model_no': 'SAME', 'color': '黑色', 'price': '10'},
              {'model_no': 'SAME', 'color': '白色', 'price': '11'}]
    result = classify('razor', parsed, [])
    assert len(result['new']) == 2, 'per-row extraction contract loses same-model color variant at classification'
