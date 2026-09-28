import sqlite3

import pytest

from catalog import db, tickets
from tests.conftest import DYNAMIC_FIELDS
from tests.test_workbook_templates import blowdryer_fixture


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


def _import_ticket(conn, tmp_path):
    from catalog.dynamic_import import build_ticket_payload
    payload = build_ticket_payload(conn, blowdryer_fixture(tmp_path), tmp_path / 'work',
                                   source_key='tickets')
    return tickets.create(conn, 'template_import', None, payload), payload


def test_import_ticket_creates_products_with_inner_code(conn, tmp_path):
    t, _ = _import_ticket(conn, tmp_path)
    r = tickets.decide(conn, t['id'], t['token'], approved=True)
    assert r['created'] == 4
    rows = conn.execute('SELECT * FROM product_dynamic').fetchall()
    assert all(row['inner_code'].startswith('KS-') for row in rows)


def test_token_is_one_time(conn, tmp_path):
    t, _ = _import_ticket(conn, tmp_path)
    tickets.decide(conn, t['id'], t['token'], approved=True)
    with pytest.raises(tickets.TicketError):
        tickets.decide(conn, t['id'], t['token'], approved=True)


def test_reject_leaves_db_untouched(conn, tmp_path):
    t, _ = _import_ticket(conn, tmp_path)
    tickets.decide(conn, t['id'], t['token'], approved=False)
    assert conn.execute('SELECT COUNT(*) c FROM product_dynamic').fetchone()['c'] == 0


def test_mutate_update_ticket(conn):
    from tests.conftest import seed_products
    seed_products(conn, [{'id': 'p1', 'inner_code': 'KS-AAAAAAAA',
                          'data': {'model': '8225', 'price': '20'}}])
    row = next(r for r in __import__('catalog').dynamic_catalog.list_products(conn, 'test_cat')
               if r['id'] == 'p1')
    t = tickets.create(conn, 'mutate', 'test_cat',
                       {'kind': 'dynamic_mutate', 'action': 'update', 'product_id': 'p1',
                        'template_version': 1, 'changes': {'price': '23'},
                        'before_snapshot': tickets._dynamic_product_snapshot(row)})
    tickets.decide(conn, t['id'], t['token'], approved=True)
    data = __import__('json').loads(conn.execute(
        "SELECT data_json FROM product_dynamic WHERE id='p1'").fetchone()[0])
    assert data['price'] == '23'


def test_manual_create_rejects_product_already_imported(conn):
    from tests.conftest import seed_products
    seed_products(conn, [{'id': 'imported', 'data': {'model': '8277', 'price': '24',
                                                    'spec': '电吹风', 'color': '白色'},
                          'status': 'approved'}])
    t = tickets.create(conn, 'mutate', 'test_cat', {
        'kind': 'dynamic_mutate', 'action': 'create', 'product_id': None,
        'template_version': 1,
        'changes': {'model': '8277', 'price': '24', 'color': '白色'}})
    with pytest.raises(tickets.TicketConflict, match='同一商品'):
        tickets.decide(conn, t['id'], t['token'], approved=True)
    assert conn.execute("select status from approval_ticket where id=?", (t['id'],)).fetchone()[0] == 'pending'
