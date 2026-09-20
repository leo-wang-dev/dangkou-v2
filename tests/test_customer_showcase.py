from tests.test_wechat_redlines import client
from catalog.main import app


def test_customer_catalog_and_photos_require_service_auth_and_observe_visibility(client, tmp_path):
    from catalog.storage import LocalStorage
    c=app.state.conn
    for pid,visible,status in [('shown',1,'approved'),('hidden',0,'approved'),('pending',1,'pending')]:
        c.execute('INSERT INTO product_razor(id,inner_code,model_no,status,cs_visible,price,remark,image_main) VALUES(?,?,?,?,?,?,?,?)',
                  (pid,pid,pid,status,visible,'SECRET_PRICE','PRIVATE_REMARK','razor/'+pid+'/main.png'))
    app.state.storage=LocalStorage(str(tmp_path))
    app.state.storage.save('razor','shown','main.png',b'image-fixture')
    c.commit()
    service_token=client.headers.get('x-service-token')
    client.headers.pop('x-service-token', None)
    assert client.get('/cs/catalog').status_code==401
    r=client.get('/cs/catalog',headers={'X-Service-Token':service_token})
    assert r.status_code==200
    assert [p['name'] for p in r.json()['products']]==['shown']
    assert 'SECRET_PRICE' not in r.text and 'PRIVATE_REMARK' not in r.text
    url='/cs/catalog/razor/shown/photo'
    assert client.get(url).status_code==401
    assert client.get(url,headers={'X-Service-Token':service_token}).content==b'image-fixture'
    c.execute("UPDATE product_razor SET cs_visible=0 WHERE id='shown'")
    c.commit()
    assert client.get(url,headers={'X-Service-Token':service_token}).status_code==404
    assert client.get('/cs/catalog',headers={'X-Service-Token':service_token}).json()['products']==[]
