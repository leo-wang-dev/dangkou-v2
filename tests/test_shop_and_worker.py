from unittest.mock import Mock
from catalog import cs
from catalog.csbot import CsBot
from tests.test_release_gates import env,auth


def test_shop_facts_require_approval_and_are_answered_verbatim(env):
    conn,client,bot=env
    changes={'address':'测试路18号','business_hours':'09:00-18:00','shipping_info':'运费由老板确认','faq':'仅批发，不零售'}
    tk=client.patch('/shop',headers=auth(),json={'changes':changes}).json()
    assert '测试路18号' not in bot._on_text({'id':'a'},'你们在哪条街')
    assert client.post(f"/tickets/{tk['ticket_id']}/decision",json={'token':tk['token'],'approved':True}).status_code==200
    for question,expected in [('你们在哪条街','测试路18号'),('几点开门','09:00-18:00'),('发货物流','运费由老板确认'),('常见问题','仅批发，不零售')]:
        assert expected in bot._on_text({'id':'a'},question)
    # Product/policy requests still go through the model's redline decision.
    bot.llm.chat_text.return_value='<<TRANSFER>> 账期问题'
    assert '老板' in bot._on_text({'id':'a'},'可以月结后发货吗')


def test_notification_worker_delivers_merchant_queue(env,monkeypatch,tmp_path):
    """删C 后出站只剩 notify*：worker 独立进程即可全量消费商家队列。"""
    conn,_,bot=env
    conn.execute("INSERT INTO cs_outbox(channel,body) VALUES('notify','merchant message')")
    conn.commit()
    sender=Mock()
    worker=CsBot(conn,None,llm=bot.llm,notifier=sender,img_dir=str(tmp_path))
    worker.flush_outbox()
    sender.assert_called_once_with('merchant message')
    assert conn.execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0').fetchone()[0]==0


def test_index_failure_is_persisted_and_recovers(env,tmp_path,monkeypatch):
    from catalog import search
    from catalog.storage import LocalStorage
    conn,_,_=env
    st=LocalStorage(str(tmp_path));rel=st.save('audit_cat','p1','photo.jpg',b'fixture')
    conn.execute('UPDATE product_dynamic SET image_main=? WHERE id=?',(rel,'p1'));conn.commit()
    embedding=Mock(side_effect=RuntimeError('network'))
    monkeypatch.setattr(search,'embed_image',embedding)
    assert search.reindex(conn,st,'audit_cat')==0
    assert conn.execute('SELECT attempts FROM embedding_retry').fetchone()[0]==1
    search.reindex(conn,st,'audit_cat');assert embedding.call_count==1
    conn.execute("UPDATE embedding_retry SET next_attempt_at=datetime('now','-1 second')");conn.commit()
    embedding.side_effect=None;embedding.return_value=[1.,0.]
    assert search.reindex(conn,st,'audit_cat')==1
    assert conn.execute('SELECT COUNT(*) FROM embedding_retry').fetchone()[0]==0
