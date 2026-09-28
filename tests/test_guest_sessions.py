import os
from tests.test_userapp import client, _new_guest, _upload, _last_code


def test_unissued_guest_cannot_read_or_upload(client):
    assert client.post('/notes', params={'guest':'guest-abcdef123456'}).status_code == 401
    assert _upload(client, 'guest-abcdef123456').status_code == 401


def test_end_revokes_all_guest_resources_and_deletes_files(client):
    guest = _new_guest(client)
    _upload(client, guest)
    note = client.post('/notes', params={'guest':guest}).json()['notes'][0]
    assert client.get('/export.xlsx', params={'guest':guest}).status_code == 200
    assert client.post('/session/end', json={'guest':guest}).status_code == 200
    assert client.post('/notes', params={'guest':guest}).status_code == 410
    assert client.get(note['photo'], params={'guest':guest}).status_code == 410
    assert client.get('/export.xlsx', params={'guest':guest}).status_code == 410
    assert client.app.state.conn.execute('SELECT count(*) FROM notes').fetchone()[0] == 0
    assert not os.listdir(client.app.state.photo_dir)


def test_idle_expiry_and_model_time_revocation(client):
    guest = _new_guest(client)
    _upload(client, guest)
    conn = client.app.state.conn
    conn.execute('UPDATE guest_sessions SET expires_at=0')
    conn.commit()
    assert client.post('/notes', params={'guest':guest}).status_code == 410
    assert not os.listdir(client.app.state.photo_dir)
    assert client.app.state.conn.execute('SELECT count(*) FROM notes').fetchone()[0] == 0


def test_verified_merge_consumes_session_and_preserves_photos(client):
    guest = _new_guest(client)
    _upload(client, guest)
    email = 'merge@example.com'
    client.post('/auth/code', json={'email':email})
    result = client.post('/auth/verify', json={'email':email,'code':_last_code(client,email),'guest':guest})
    assert result.status_code == 200
    auth = {'Authorization':'Bearer '+result.json()['token']}
    assert client.post('/notes', params={'guest':guest}).status_code == 410
    note = client.post('/notes', headers=auth).json()['notes'][0]
    assert client.get(note['photo'], headers=auth).status_code == 200
    client.post('/guest')  # cleanup cannot remove merged files
    assert client.get('/export.xlsx', headers=auth).status_code == 200
    assert client.post('/session/end', headers=auth).status_code == 200
    assert client.post('/notes', headers=auth).status_code == 401


def test_shop_list_capability_refreshes_idle_timeout(h5):
    import time
    from fastapi.testclient import TestClient
    app, database, photo = h5
    with TestClient(app) as c:
        guest=c.post('/cs/chat/test-shop/session').json()['visitor']
        c.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'})
        c.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('x.jpg',photo)})
        token=c.get('/cs/chat/test-shop/list-token',params={'visitor':guest}).json()['token']
        from catalog.guest_sessions import digest
        app.state.conn.execute('UPDATE guest_sessions SET expires_at=? WHERE token_hash=?',(time.time()+60,digest(guest)))
        app.state.conn.commit()
        assert c.get('/cs/link/'+token).status_code==200
        expiry=app.state.conn.execute('SELECT expires_at FROM guest_sessions WHERE token_hash=?',(digest(guest),)).fetchone()[0]
        assert expiry > time.time()+86000

from tests.test_h5_transactions import h5


def test_periodic_cleanup_removes_expired_rows_orphans_but_preserves_user_references(client, tmp_path):
    import time
    from catalog import guest_sessions
    guest=_new_guest(client)
    _upload(client,guest)
    conn=client.app.state.conn
    path=conn.execute('SELECT photo_path FROM notes').fetchone()[0]
    conn.execute("INSERT INTO notes(owner_kind,owner_id,photo_path) VALUES('user','saved@example.com',?)",(path,))
    conn.execute('UPDATE guest_sessions SET expires_at=?',(time.time()-172800,));conn.commit()
    orphan=os.path.join(client.app.state.photo_dir,'orphan.jpg');open(orphan,'wb').write(b'orphan');os.utime(orphan,(1,1))
    guest_sessions.sweep(conn,'central',client.app.state.photo_dir)
    assert conn.execute('SELECT count(*) FROM guest_sessions').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM notes').fetchone()[0]==1
    assert os.path.isfile(path) and not os.path.exists(orphan)


def test_upgrade_revokes_legacy_unscoped_list_capability(h5):
    from fastapi.testclient import TestClient
    from catalog import db
    app,_,_=h5
    conn=app.state.conn
    conn.execute("INSERT INTO cs_customer(id,tg_id) VALUES('legacy','h5-browser-chosen')")
    conn.execute("INSERT INTO cs_link(token,customer_id) VALUES('legacy-public-link','legacy')")
    conn.commit()
    db.init_db(conn)
    with TestClient(app) as c:
        assert c.get('/cs/link/legacy-public-link').status_code==410
