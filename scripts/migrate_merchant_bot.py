"""Explicit offline bot replacement, retaining customer notes and an audit archive.
Stop the old customer worker first. Usage: python scripts/migrate_merchant_bot.py MERCHANT_ID
"""
import fcntl
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import db,merchant_onboarding as hub
from catalog.merchant_binding import credentials


def migrate(c,mid,activation=False):
    m=c.execute('SELECT * FROM merchant WHERE id=?',(mid,)).fetchone()
    if not m or not m['db_path'] or not m['bot_id'] or not m['invitation_used']:
        raise ValueError('必须先通过档口邀请码认领并绑定新 bot')
    if not activation and (m['state']=='enabled' or m['runtime_status'] not in ('stopped','error')):
        raise ValueError('请先暂停客服并等待 worker 停止')
    path=Path(m['db_path']); secret=json.loads(credentials(mid).read_text())
    if secret['bot_token'].split(':')[0]!=m['bot_id']:raise ValueError('bot 身份不匹配')
    with open(str(path)+'.bot.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source=db.connect(str(path))
        try:
            old=source.execute('SELECT tg_bot_id FROM shop_profile WHERE id=1').fetchone()[0]
            if old==m['bot_id']:return '已经完成迁移'
            if activation:
                import os
                platform=os.environ.get('TG_BOT_TOKEN','').split(':')[0]
                if not platform or old!=platform or m['state']!='enabled':
                    raise ValueError('自动迁移仅限已认领的原平台 bot 档口，其他绑定需要管理员处理')
            if source.execute('SELECT 1 FROM cs_inbox WHERE processed=0 LIMIT 1').fetchone():raise ValueError('旧 bot 仍有待处理消息，请先排空')
            if source.execute("SELECT 1 FROM cs_outbox WHERE sent=0 AND channel IN ('tg','tg_document') LIMIT 1").fetchone():raise ValueError('旧 bot 仍有待发送消息，请先排空')
            stamp=str(time.time_ns())
            backup=path.with_name(path.name+'.before-bot-'+stamp)
            # Backup before mutation, restrict permissions before SQLite writes data.
            import os
            fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
            target=db.connect(str(backup));source.backup(target);target.close()
            source.execute('BEGIN IMMEDIATE')
            for table in ('cs_inbox','cs_outbox'):
                source.execute(f'CREATE TABLE {table}_archive_{stamp} AS SELECT * FROM {table}')
            last=source.execute('SELECT MAX(update_id) FROM cs_inbox').fetchone()[0]
            source.execute('DELETE FROM cs_inbox')
            source.execute("DELETE FROM cs_outbox WHERE channel IN ('tg','tg_document')")
            source.execute('UPDATE shop_profile SET tg_bot_id=?,tg_bot_username=? WHERE id=1',(m['bot_id'],m['bot_username']))
            source.commit()
            return {'backup':str(backup),'old_bot_last_update':last,'new_bot_id':m['bot_id']}
        finally:source.close()

if __name__=='__main__':
    c=hub.connect()
    try:print(migrate(c,sys.argv[1]))
    finally:c.close()
