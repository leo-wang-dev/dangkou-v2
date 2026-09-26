"""Different pending imports must not overwrite a newer approved source snapshot.

动态分类版：同一来源的两张在途导入工单，先批的生效、后批的因商品数据已变化被拒；
外部改动让过期工单不能落库；不同来源互不影响。
"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from catalog import db, tickets
from catalog.dynamic_import import build_ticket_payload
from tests.test_workbook_templates import blowdryer_fixture


@pytest.fixture
def setup(tmp_path):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    path = blowdryer_fixture(tmp_path)
    work = tmp_path / 'work'

    def new(source='supplier'):
        payload = build_ticket_payload(conn, path, work, source_key=source)
        ticket = tickets.create(conn, 'template_import', None, payload)
        return dict(conn.execute('SELECT * FROM approval_ticket WHERE id=?',
                                 (ticket['id'],)).fetchone())
    yield conn, new, path, work
    conn.close()


def approve(conn, tk):
    return tickets.decide(conn, tk['id'], tk['token'], True)


def _count(conn):
    return conn.execute('SELECT COUNT(*) FROM product_dynamic').fetchone()[0]


def test_two_pending_initial_imports_cannot_duplicate(setup):
    conn, new = setup[0], setup[1]
    first, second = new(), new()
    approve(conn, first)
    with pytest.raises(tickets.TicketError, match='变化|过期|冲突'):
        approve(conn, second)
    assert _count(conn) == 4
    assert conn.execute('SELECT status FROM approval_ticket WHERE id=?',
                        (second['id'],)).fetchone()[0] == 'pending'


def test_parallel_approvals_only_one_snapshot_wins(setup):
    conn, new = setup[0], setup[1]
    first, second = new(), new()

    def run(tk):
        try:
            approve(conn, tk)
            return 'approved'
        except tickets.TicketError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [first, second])) == ['approved', 'conflict']
    assert _count(conn) == 4


def test_external_edit_blocks_stale_update_and_allows_rejection(setup):
    conn, new, path, work = setup
    from openpyxl import load_workbook
    approve(conn, new())
    workbook = load_workbook(path); workbook['吹风机']['C3'] = '新颜色'; workbook.save(path)
    pending = new()
    row = conn.execute('SELECT id, category_key, data_json FROM product_dynamic LIMIT 1').fetchone()
    data = json.loads(row['data_json'])
    key = sorted(data)[0]                     # 改任一真实字段都算外部改动
    data[key] = '外部改动'
    conn.execute('UPDATE product_dynamic SET data_json=? WHERE id=?',
                 (json.dumps(data, ensure_ascii=False), row['id']))
    conn.commit()
    with pytest.raises(tickets.TicketError):
        approve(conn, pending)
    assert json.loads(conn.execute('SELECT data_json FROM product_dynamic WHERE id=?',
                                   (row['id'],)).fetchone()[0])[key] == '外部改动'
    tickets.decide(conn, pending['id'], pending['token'], False)


def test_independent_sources_and_reimport_after_conflict(setup):
    conn, new = setup[0], setup[1]
    a, b = new('a'), new('b')
    approve(conn, a); approve(conn, b)
    assert _count(conn) == 8                   # 不同来源各自成行，互不合并
    tk = new('a'); approve(conn, tk)            # 同源重导：无变化不重复建行
    assert _count(conn) == 8
