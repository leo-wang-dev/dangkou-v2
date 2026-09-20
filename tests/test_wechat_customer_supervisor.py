from catalog import db, merchant_policy
from scripts import run_wechat_customer


def test_binding_snapshot_is_read_only_after_service_start(tmp_path, monkeypatch):
    path = tmp_path / 'shop.db'
    conn = db.connect(str(path))
    db.init_db(conn)
    conn.execute("UPDATE shop_profile SET tg_bot_id='222222' WHERE id=1")
    merchant_policy.apply(conn, {'wechat_managed': True}, 1)
    conn.commit()
    conn.close()
    monkeypatch.setattr(run_wechat_customer.config, 'DB_PATH', str(path))
    monkeypatch.setattr(run_wechat_customer.db, 'init_db',
                        lambda *_: (_ for _ in ()).throw(AssertionError('heartbeat must not migrate the live DB')))

    profile, policy = run_wechat_customer.binding_snapshot()

    assert profile['tg_bot_id'] == '222222'
    assert policy == {'wechat_managed': True}


def test_healthy_child_with_unchanged_credentials_needs_no_binding_check():
    class RunningChild:
        def poll(self):
            return None

    assert not run_wechat_customer.needs_binding_check(RunningChild(), 'same', 'same')
    assert run_wechat_customer.needs_binding_check(None, 'same', 'same')
    assert run_wechat_customer.needs_binding_check(RunningChild(), 'old', 'new')
