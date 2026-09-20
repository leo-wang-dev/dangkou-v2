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


def test_notification_worker_independent_of_tg_and_no_double_consumer(env,monkeypatch,tmp_path):
    conn,_,bot=env
    monkeypatch.setenv('CATALOG_NOTIFY_WORKER','1')
    conn.execute("INSERT INTO cs_outbox(channel,body) VALUES('notify','merchant message')")
    conn.execute("INSERT INTO cs_outbox(channel,recipient,body) VALUES('tg','123','buyer message')")
    conn.commit()
    bot.flush_outbox()
    assert bot.api.send_message.call_count==1 and not bot.notifier.called
    sender=Mock()
    worker=CsBot(conn,None,llm=bot.llm,notifier=sender,img_dir=str(tmp_path))
    worker.flush_outbox(notifications_only=True)
    sender.assert_called_once_with('merchant message')
    assert conn.execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0').fetchone()[0]==0


def test_pending_message_recovers_even_when_tg_poll_is_down(env, monkeypatch):
    import json
    from scripts import run_cs_bot
    conn,_,bot=env
    conn.execute('INSERT INTO cs_inbox(update_id,payload) VALUES(?,?)',(901,json.dumps({'update_id':901,'message':{'chat':{'id':321},'from':{'id':321},'text':'你好'}})))
    conn.commit()
    conn.execute("UPDATE shop_profile SET shop_name='测试档口',tg_bot_id='12345'");conn.commit()
    api=Mock();api._call.return_value={'id':12345,'is_bot':True}
    api.poll.side_effect=[RuntimeError('network down'),KeyboardInterrupt()]
    monkeypatch.setattr(run_cs_bot.db,'connect',lambda:conn)
    monkeypatch.setattr(run_cs_bot,'TgApi',lambda:api)
    monkeypatch.setattr(run_cs_bot,'CsBot',lambda *args:bot)
    monkeypatch.setattr(run_cs_bot.time,'sleep',lambda _:None)
    run_cs_bot.main()
    assert conn.execute('SELECT processed FROM cs_inbox WHERE update_id=901').fetchone()[0]==1


def test_failed_message_backoff_keeps_same_customer_order(env):
    import json
    from scripts.run_cs_bot import process_pending
    conn,_,bot=env
    for uid,cid in [(801,1),(802,1),(803,2)]:
        conn.execute('INSERT INTO cs_inbox(update_id,payload) VALUES(?,?)',(uid,json.dumps({'update_id':uid,'message':{'chat':{'id':cid}}})))
    conn.commit()
    stub=Mock();stub.handle_update.side_effect=RuntimeError('bad model')
    assert process_pending(conn,stub)==2
    assert [c.args[0]['update_id'] for c in stub.handle_update.call_args_list]==[801,803]
    assert process_pending(conn,stub)==0
    assert conn.execute('SELECT attempts FROM cs_inbox WHERE update_id=802').fetchone()[0]==0


def test_index_failure_is_persisted_and_recovers(env,tmp_path,monkeypatch):
    from catalog import search
    from catalog.storage import LocalStorage
    conn,_,_=env
    st=LocalStorage(str(tmp_path));rel=st.save('curler','p1','photo.jpg',b'fixture')
    conn.execute('UPDATE product_curler SET image_main=? WHERE id=?',(rel,'p1'));conn.commit()
    embedding=Mock(side_effect=RuntimeError('network'))
    monkeypatch.setattr(search,'embed_image',embedding)
    assert search.reindex(conn,st,'curler')==0
    assert conn.execute('SELECT attempts FROM embedding_retry').fetchone()[0]==1
    search.reindex(conn,st,'curler');assert embedding.call_count==1
    conn.execute("UPDATE embedding_retry SET next_attempt_at=datetime('now','-1 second')");conn.commit()
    embedding.side_effect=None;embedding.return_value=[1.,0.]
    assert search.reindex(conn,st,'curler')==1
    assert conn.execute('SELECT COUNT(*) FROM embedding_retry').fetchone()[0]==0
