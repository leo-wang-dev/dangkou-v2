"""One durable polling owner for the platform onboarding bot."""
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config, merchant_onboarding as hub
from catalog.tg import TgApi

TOKEN = re.compile(r'\b\d{5,20}:[A-Za-z0-9_-]{20,100}\b')

def ingest(c, updates):
    for u in updates:
        msg=u.get('message') or {}; sender=msg.get('from') or {}; chat=msg.get('chat') or {}
        valid=chat.get('type')=='private' and sender.get('id')==chat.get('id') and not sender.get('is_bot')
        # Persist only necessary authenticated fields, never raw Telegram payloads/captions.
        value=msg.get('text','') if valid else ''
        value='__TOKEN_REDACTED__' if TOKEN.search(value) else value[:2400]
        payload={'owner':str(sender.get('id')), 'text':value} if valid else {}
        c.execute('INSERT OR IGNORE INTO hub_inbox(update_id,payload) VALUES(?,?)',(u['update_id'],json.dumps(payload)))
    c.commit()

def process(c):
    blocked=set()
    for row in c.execute('SELECT * FROM hub_inbox WHERE processed=0 ORDER BY update_id LIMIT 100').fetchall():
        p=json.loads(row['payload'])
        if p.get('owner') in blocked:continue
        try:
            c.execute('BEGIN IMMEDIATE')
            p=json.loads(row['payload'])
            if p:
                answer=('请勿在聊天中发送 Token。请在 BotFather 撤销刚才的 Token，再回复“绑定客服”使用安全页面。' if p['text']=='__TOKEN_REDACTED__' else hub.handle(c,p['owner'],p['text']))
                for i in range(0,len(answer),3500):
                    c.execute('INSERT INTO hub_outbox(recipient,body) VALUES(?,?)',(p['owner'],answer[i:i+3500]))
            c.execute('UPDATE hub_inbox SET processed=1,error=NULL WHERE update_id=?',(row['update_id'],));c.commit()
        except Exception as e:
            c.rollback()
            c.execute('UPDATE hub_inbox SET error=? WHERE update_id=?',(type(e).__name__,row['update_id']));c.commit()
            blocked.add(p.get('owner'))

def flush(c,api):
    blocked=set()
    for row in c.execute('SELECT * FROM hub_outbox WHERE sent=0 ORDER BY id LIMIT 100').fetchall():
        if row['recipient'] in blocked:continue
        try:
            api.send_message(int(row['recipient']),row['body'])
            c.execute('UPDATE hub_outbox SET sent=1 WHERE id=?',(row['id'],))
        except Exception:
            blocked.add(row['recipient'])
            c.execute('UPDATE hub_outbox SET attempts=attempts+1 WHERE id=?',(row['id'],))
        c.commit()

def main():
    c=hub.connect(); api=TgApi(); api._call('getMe')
    path=c.execute('PRAGMA database_list').fetchone()[2]
    with open(path+'.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                process(c);flush(c,api)
                last=c.execute('SELECT MAX(update_id) FROM hub_inbox').fetchone()[0]
                api._offset=last+1 if last is not None else int(os.environ.get('MERCHANT_HUB_INITIAL_OFFSET','0'))
                ingest(c,api.poll());process(c);flush(c,api)
            except KeyboardInterrupt:return
            except Exception as e:
                c.rollback();print('merchant-hub:',type(e).__name__,flush=True);time.sleep(5)

if __name__=='__main__':main()
