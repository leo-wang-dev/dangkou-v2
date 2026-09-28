import io
import json
import openpyxl
from tests.test_userapp import client, _new_guest, _upload, _last_code


def test_confirmed_cards_affect_only_subsequent_notes_and_export_groups(client):
    guest = _new_guest(client)
    _upload(client, guest)
    for name in ('A/Shop', 'A:Shop'):
        client.app.state.llm.vision_reply = json.dumps([{'名片':{'档口名称':name,'供应商联系人':name+' contact'}}])
        response = _upload(client, guest).json()
        assert response['pending_batches']
        batch_id = response['pending_batches'][0]['id']
        assert client.post('/batches/confirm', params={'guest':guest}, json={'batch_id':batch_id}).status_code == 200
        client.app.state.llm.vision_reply = json.dumps([{'型号或品名':name+' item'}])
        _upload(client, guest)
    notes = client.post('/notes', params={'guest':guest}).json()['notes']
    assert notes[0]['fields']['档口名称'] == '待补充'
    assert [n['fields']['档口名称'] for n in notes[1:]] == ['A/Shop','A:Shop']
    wb = openpyxl.load_workbook(io.BytesIO(client.get('/export.xlsx',params={'guest':guest}).content))
    assert len(wb.worksheets) == 3
    assert len(set(s.casefold() for s in wb.sheetnames)) == 3
    assert all(len(s)<=31 and not any(c in s for c in '\\/*?:[]') for s in wb.sheetnames)
    assert 'A/Shop contact' in str(list(wb.worksheets[1].values))
    assert 'A:Shop contact' not in str(list(wb.worksheets[1].values))


def test_card_with_product_needs_explicit_association_and_owner_scope(client):
    guest = _new_guest(client)
    client.app.state.llm.vision_reply = json.dumps([{'名片':{'档口名称':'Card'}},{'型号或品名':'Item'}])
    body = _upload(client, guest).json()
    bid = body['pending_batches'][0]['id']
    note = client.post('/notes', params={'guest':guest}).json()['notes'][0]
    assert note['fields']['档口名称'] == '待补充'
    other = _new_guest(client)
    assert client.post('/batches/confirm',params={'guest':other},json={'batch_id':bid}).status_code == 404
    assert client.post('/batches/confirm',params={'guest':guest},json={'batch_id':bid,'note_ids':[note['id']]}).status_code == 200
    assert client.post('/notes',params={'guest':guest}).json()['notes'][0]['fields']['档口名称'] == 'Card'


def test_merge_preserves_batch_cards_and_association(client):
    guest=_new_guest(client)
    client.app.state.llm.vision_reply=json.dumps([{'名片':{'档口名称':'Preserved'}}])
    bid=_upload(client,guest).json()['pending_batches'][0]['id']
    client.post('/batches/confirm',params={'guest':guest},json={'batch_id':bid})
    client.app.state.llm.vision_reply=json.dumps([{'型号或品名':'Goods'}])
    _upload(client,guest)
    client.post('/auth/code',json={'email':'card@example.com'})
    token=client.post('/auth/verify',json={'email':'card@example.com','code':_last_code(client,'card@example.com'),'guest':guest}).json()['token']
    auth={'Authorization':'Bearer '+token}
    data=client.post('/notes',headers=auth).json()
    assert data['notes'][0]['batch_id']==bid
    assert data['notes'][0]['fields']['档口名称']=='Preserved'
    assert data['batches'][0]['owner_kind']=='user'
    assert _upload(client,'',token=token).status_code==200
    assert client.post('/notes',headers=auth).json()['notes'][-1]['batch_id']==bid


def test_shop_card_and_product_do_not_silently_associate_and_manual_edit_detaches(h5, monkeypatch):
    from fastapi.testclient import TestClient
    from catalog import llm
    app,database,photo=h5
    monkeypatch.setattr(llm,'chat_vision',lambda *a,**kw:json.dumps([{'名片':{'档口名称':'A'}},{'型号或品名':'Item','档口名称':'A'}]))
    with TestClient(app) as c:
        guest=c.post('/cs/chat/test-shop/session').json()['visitor']
        c.post('/cs/chat/test-shop/mode',json={'visitor':guest,'mode':'notes'})
        c.post('/cs/chat/test-shop/photo',data={'visitor':guest},files={'file':('x.jpg',photo)})
        state=c.get('/cs/chat/test-shop/session',params={'visitor':guest}).json()
        n=state['notes'][0]
        assert n['fields']['档口名称']=='待补充'
        bid=next(b['id'] for b in state['batches'] if b['state']=='pending')
        c.post('/cs/chat/test-shop/batches/confirm',json={'visitor':guest,'batch_id':bid,'note_ids':[n['id']]})
        link=c.get('/cs/chat/test-shop/list-token',params={'visitor':guest}).json()['token']
        edited=c.patch('/cs/link/'+link+'/note/'+str(n['id']),json={'field':'档口名称','value':'B'})
        assert edited.json()['fields']['档口名称']=='B'
        assert app.state.conn.execute('SELECT fields_json FROM note_batches WHERE id=?',(bid,)).fetchone()[0]=='{"档口名称": "A"}'

from tests.test_h5_transactions import h5


def test_text_notes_share_explicit_current_batch(h5):
    from catalog import note_batches, purchase_notes
    from catalog.csbot import CsBot
    app,_,_=h5
    bot=CsBot(app.state.conn,None)
    cust=bot._ensure_customer({'id':'text-test'})
    bid=note_batches.create(app.state.conn,'guest',cust['id'],{'档口名称':'Text Supplier'},'pending')
    note_batches.confirm(app.state.conn,'guest',cust['id'],bid,[],'cs_note')
    note=purchase_notes.create(bot,cust,{'型号或品名':'Text Goods'})
    assert note['batch_id']==bid
