import sqlite3

import pytest
from fastapi.testclient import TestClient

from catalog import db
from catalog.main import app
from catalog.storage import LocalStorage


@pytest.fixture()
def client(tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    app.state.conn = conn
    app.state.token = 'T0KEN'
    app.state.storage = LocalStorage(str(tmp_path))
    app.state.callback = None
    return TestClient(app)


def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200 and r.json()['status'] == 'ready'


def test_img_requires_token(client, tmp_path):
    client.app.state.storage.save('razor', 'id1', 'main.png', b'PNG')
    assert client.get('/img/razor/id1/main.png').status_code == 401
    r = client.get('/img/razor/id1/main.png', headers={'X-Service-Token': 'T0KEN'})
    assert r.status_code == 200 and r.content == b'PNG'
