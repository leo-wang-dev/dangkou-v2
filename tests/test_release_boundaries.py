"""Additional offline boundary checks; correct business behavior is asserted."""
import io
import json
from unittest.mock import Mock

import openpyxl
import pytest

from tests.test_release_gates import env, auth, take_photo, say
from catalog import cs, tickets


def conn_safe(bot):
    return not bot.conn.in_transaction


@pytest.mark.parametrize('tiers', ['20:12;20:11', '-20:12', '0.5:12', '20:12;50:-11'])
def test_invalid_tier_table_not_accepted(env, tiers):
    _, client, _ = env
    response = client.patch('/products/audit_cat/p1', headers=auth(),
                            json={'changes': {'可观测': '1', '阶梯价': tiers}})
    assert response.status_code == 400, f'invalid or partial tier table accepted: {tiers}'


def test_visibility_accepts_only_exact_zero_one(env):
    conn, client, bot = env
    conn.execute("UPDATE product_dynamic SET cs_visible=0 WHERE id='p1'"); conn.commit()
    # 布尔 True 不再被接受（动态契约只认 '0'/'1' 字符串）
    assert client.patch('/products/audit_cat/p1', headers=auth(),
                        json={'changes': {'可观测': True}}).status_code == 400
    response = client.patch('/products/audit_cat/p1', headers=auth(),
                            json={'changes': {'可观测': '1'}})
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
    take_photo(bot)
    bot.llm.chat_text.return_value = bad_json
    reply = say(bot, 100, '第一个改价')      # 坏模型输出必须安全回落，不抛错不写脏草稿
    assert isinstance(reply, str) and reply
    assert conn_safe(bot)


def test_same_customer_text_not_duplicated_in_llm_history(env):
    _, _, bot = env
    say(bot, 100, 'MODEL-1 介绍一下')
    messages = bot.llm.chat_text.call_args.args[1]
    assert sum(m['content'] == 'MODEL-1 介绍一下' for m in messages) == 1


def test_bot_two_buyers_photo_confirmation_stays_separate(env):
    conn, _, bot = env
    take_photo(bot)
    take_photo(bot, 200)
    say(bot, 100, '确认')
    rows = conn.execute("SELECT c.tg_id,n.status FROM cs_note n JOIN cs_customer c ON c.id=n.customer_id WHERE c.tg_id IN ('100','200') ORDER BY c.tg_id").fetchall()
    assert [tuple(r) for r in rows] == [('100', 'confirmed'), ('200', 'draft')]


def test_customer_photo_does_not_write_merchant_catalog(env):
    conn, _, bot = env
    before = conn.execute('SELECT COUNT(*) FROM product_dynamic').fetchone()[0]
    take_photo(bot)
    say(bot, 100, '确认')
    assert conn.execute('SELECT COUNT(*) FROM product_dynamic').fetchone()[0] == before


def test_hidden_and_delisted_products_not_in_brief(env):
    conn, _, bot = env
    conn.execute("UPDATE product_dynamic SET cs_visible=0 WHERE id='p1'")
    conn.commit()
    assert 'MODEL-1' not in bot._catalog_brief()
    conn.execute("UPDATE product_dynamic SET cs_visible=1,status='delisted' WHERE id='p1'")
    conn.commit()
    assert 'MODEL-1' not in bot._catalog_brief()


def test_import_preview_does_not_disclose_service_token(env):
    conn, client, _ = env
    payload = {'kind': 'template_import', 'work_dir': '/tmp/audit-only', 'sheets': [{
        'template': {'key': 'audit_cat', 'name': '审计品类', 'version': 1,
                     'fields': [], 'storage': 'dynamic', 'source_sheet': '审计品类'},
        'template_action': 'create', 'expected_version': 0, 'title': '审计品类',
        'header_row': 1, 'image_count': 0, 'source_sheet': '审计品类', 'source_snapshot': '',
        'drafts': {'new': [{'data': {'model': 'P'}, 'images': ['a.jpg'],
                            'image_main': 'a.jpg'}],
                   'update': [], 'delist': []}}]}
    tk = tickets.create(conn, 'template_import', None, payload)
    response = client.get(f'/tickets/{tk["id"]}?t={tk["token"]}')
    assert response.status_code == 200
    assert 'audit-service-secret' not in response.text, 'anonymous preview reveals service-wide write credential'


def test_import_keeps_separate_rows_with_same_model():
    from catalog.dynamic_import import _classify_rows
    incoming = [{'data': {'model': 'SAME', 'color': '黑色'}, 'images': [],
                 'image_main': '', 'source_row': None, 'row_fingerprint': 'fp-1'},
                {'data': {'model': 'SAME', 'color': '白色'}, 'images': [],
                 'image_main': '', 'source_row': None, 'row_fingerprint': 'fp-2'}]
    result = _classify_rows([], incoming, ['model'])
    assert len(result['new']) == 2, 'per-row extraction contract loses same-model color variant at classification'
