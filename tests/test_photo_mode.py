import json
import math
from PIL import Image
from catalog.csbot import CsBot, extract_photo_items


def test_review_preserves_product_boxes_by_unique_name_not_order(tmp_path):
    image = Image.new('RGB', (200, 100), 'red')
    image.paste('blue', (100, 0, 200, 100))
    path = str(tmp_path / 'two.png')
    image.save(path)
    class Model:
        calls = 0
        def chat_vision(self, prompt, data):
            self.calls += 1
            return json.dumps(([{'型号或品名': 'Red', '图框': [0,0,500,1000]},
                                {'型号或品名': 'Blue', '图框': [500,0,1000,1000]}]
                               if self.calls == 1 else [{'型号或品名': 'Blue'}, {'型号或品名': 'Red'}]))
    items, _ = extract_photo_items(Model(), b'image')
    for i, (item, expected) in enumerate(zip(items, [(0,0,255), (255,0,0)])):
        crop = CsBot._crop_photo(path, i, item.get('__图框__'))
        with Image.open(crop) as im:
            assert im.size == (100,100)
            assert all(abs(a-b)<5 for a,b in zip(im.getpixel((50,50)), expected))


def test_product_box_rejects_invalid_geometry_and_duplicate_matches():
    for box in ([500,0,100,1000], [0,0,math.inf,1000], [0,0,0,500]):
        assert '__图框__' not in CsBot._parse_items(json.dumps([{'型号或品名':'X','图框':box}]))[0]
    initial = [{'型号或品名':'X', '价格':'未拍到', '__图框__':[0,0,500,1000]}] * 2
    result = CsBot._conservative_prices(initial, [{'型号或品名':'X','价格':'未拍到'}])
    assert '__图框__' not in result[0]

from tests.test_h5_transactions import h5
from fastapi.testclient import TestClient
from catalog import llm, cs


def test_shop_intent_queue_search_notes_and_revocation(h5, monkeypatch):
    app, database, photo = h5
    with TestClient(app) as client:
        assert client.post('/cs/chat/test-shop/message',json={'visitor':'invented','text':'hi'}).status_code == 401
        guest = client.post('/cs/chat/test-shop/session').json()['visitor']
        calls = []
        def vision(*args, **kwargs):
            calls.append(args)
            return json.dumps([{'型号或品名':'Sample'}])
        monkeypatch.setattr(llm,'chat_vision',vision)
        response = client.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('x.jpg',photo)})
        assert response.json()['status'] == 'intent_required'
        assert calls == []
        response = client.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'search'})
        assert response.json()['photo_mode'] == 'search'
        assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0] == 0
        client.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'})
        response = client.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('x.jpg',photo)})
        assert response.status_code == 200
        assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0] == 1
        token = client.get('/cs/chat/test-shop/list-token',params={'visitor':guest}).json()['token']
        assert client.get('/cs/link/'+token+'/export.xlsx').status_code == 200
        assert client.post('/cs/chat/test-shop/session/end',json={'visitor':guest}).status_code == 200
        assert client.get('/cs/link/'+token).status_code in (404,410)
        assert client.get('/cs/chat/test-shop/list-token',params={'visitor':guest}).status_code == 410


def test_shop_photo_approved_redline_durably_hands_off(h5, monkeypatch):
    app, database, photo = h5
    app.state.conn.execute("INSERT INTO cs_redline(product_id,text_raw,text_summary) VALUES('','红色商品转人工','红色商品转人工')")
    app.state.conn.commit()
    monkeypatch.setattr(llm,'chat_vision',lambda prompt,*a,**kw: 'TRANSFER' if '规则匹配' in prompt else json.dumps([{'型号或品名':'Red'}]))
    with TestClient(app) as client:
        guest = client.post('/cs/chat/test-shop/session').json()['visitor']
        client.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'})
        response = client.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('x.jpg',photo)})
        assert response.status_code == 200
    assert app.state.conn.execute('SELECT count(*) FROM cs_outbox').fetchone()[0] == 1


def test_review_explicit_null_does_not_restore_initial_box():
    initial = CsBot._parse_items('[{"型号或品名":"X","图框":[0,0,500,1000]}]')
    reviewed = CsBot._parse_items('[{"型号或品名":"X","图框":null}]')
    assert '__图框__' not in CsBot._conservative_prices(initial,reviewed)[0]


def test_revoked_during_vision_cannot_commit_or_leave_files(h5, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    app, database, photo = h5
    started, release = threading.Event(), threading.Event()
    def vision(*a,**kw):
        started.set()
        assert release.wait(5)
        return json.dumps([{'型号或品名':'Late'}])
    monkeypatch.setattr(llm,'chat_vision',vision)
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        guest=client.post('/cs/chat/test-shop/session').json()['visitor']
        client.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'})
        future=pool.submit(client.post,'/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('p.jpg',photo)})
        try:
            assert started.wait(2)
            assert client.post('/cs/chat/test-shop/session/end',json={'visitor':guest}).status_code==200
        finally:
            release.set()
        assert future.result().status_code==410
    assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0]==0
    assert not list((database.parent/'photos').glob('*'))


def test_double_pending_mode_submission_has_one_effect(h5, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    app,database,photo=h5
    barrier=threading.Barrier(2)
    local=threading.local()
    def vision(*a,**kw):
        if not getattr(local,'seen',False):
            local.seen=True
            barrier.wait(timeout=5)
        return json.dumps([{'型号或品名':'Once'}])
    monkeypatch.setattr(llm,'chat_vision',vision)
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        guest=client.post('/cs/chat/test-shop/session').json()['visitor']
        client.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('p.jpg',photo)})
        futures=[pool.submit(client.post,'/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'}) for _ in range(2)]
        assert sorted(f.result().status_code for f in futures)==[200,409]
    assert app.state.conn.execute('SELECT count(*) FROM cs_note').fetchone()[0]==1
    assert len(list((database.parent/'photos').glob('*')))==1
