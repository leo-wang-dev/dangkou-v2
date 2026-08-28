import sqlite3

import pytest

from catalog import db, tickets


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    return c


def test_import_ticket_creates_products_with_inner_code(conn):
    t = tickets.create(conn, 'import', 'razor',
                       {'kind': 'import', 'work_dir': None,
                        'drafts': {'new': [{'model_no': '8225', 'price': '21.5'}],
                                   'update': [], 'delist': []}})
    r = tickets.decide(conn, t['id'], t['token'], approved=True)
    assert r['created'] == 1 and r['created_rows'][0]['image_main'] == ''
    row = conn.execute('SELECT * FROM product_razor').fetchone()
    assert row['model_no'] == '8225' and row['inner_code'].startswith('KS-')


def test_token_is_one_time(conn):
    t = tickets.create(conn, 'import', 'razor',
                       {'kind': 'import', 'work_dir': None,
                        'drafts': {'new': [], 'update': [], 'delist': []}})
    tickets.decide(conn, t['id'], t['token'], approved=True)
    with pytest.raises(tickets.TicketError):
        tickets.decide(conn, t['id'], t['token'], approved=True)


def test_reject_leaves_db_untouched(conn):
    t = tickets.create(conn, 'import', 'razor',
                       {'kind': 'import', 'work_dir': None,
                        'drafts': {'new': [{'model_no': '8225'}],
                                   'update': [], 'delist': []}})
    tickets.decide(conn, t['id'], t['token'], approved=False)
    assert conn.execute('SELECT COUNT(*) c FROM product_razor').fetchone()['c'] == 0


def test_mutate_update_ticket(conn):
    conn.execute("INSERT INTO product_razor(id, inner_code, model_no, price) "
                 "VALUES('p1','KS-AAAAAAAA','8225','20')")
    conn.commit()
    t = tickets.create(conn, 'mutate', 'razor',
                       {'kind': 'mutate', 'action': 'update', 'product_id': 'p1',
                        'changes': {'price': '23'}})
    tickets.decide(conn, t['id'], t['token'], approved=True)
    assert conn.execute("SELECT price FROM product_razor WHERE id='p1'").fetchone()['price'] == '23'
