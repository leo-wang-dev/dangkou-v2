"""Merchant-owned setup drafts. Control-bot identity is never shop ownership."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

STEPS = [
 ('shop_name', '你的档口名称是什么？'),
 ('owner_tg_username', '客户转人工时应联系哪个老板 TG Username？请填写 @用户名，也可以回复“跳过”。'),
 ('owner_wechat', '老板的微信号是什么？可以回复“跳过”。'),
 ('quote_rules', '你希望客户看到什么报价规则？请用自己的话填写。我们不替你制定档位、底价或计算报价；未填写不会自动报价格。可以跳过。'),
 ('price', '哪些价格或议价问题需要你接手？例如最低价、长期优惠、整批库存、样品费抵扣。都是参考，不默认开启，可以跳过。'),
 ('payment', '哪些付款条件需要转人工？例如月结、赊账、信用证、延长账期。可自行限定条件，也可跳过。'),
 ('custom', '哪些定制或打样问题交给你？例如开模、颜色、包装、品牌标签、尺寸、工艺。可以跳过。'),
 ('logistics', '哪些物流或交期问题交给你？例如加急、货代、指定仓库、拼柜、代发、暂存、报关。可以只设置其中一项，也可以跳过。'),
 ('after_sales', '哪些售后问题交给你？例如次品赔付、退货、质保、延期损失、运输破损。可以跳过。'),
 ('other', '还有其他需要你亲自处理的情况吗？没有就回复“跳过”。'),
]
RULE_KEYS = {k for k, _ in STEPS[4:]}


def connect():
    from . import config
    p = Path(os.environ.get('MERCHANT_HUB_DB', str(Path(config.DB_PATH).parent / 'merchant-hub.db')))
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS merchant(
        id TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, state TEXT NOT NULL DEFAULT 'draft',
        step INTEGER NOT NULL DEFAULT 0, draft TEXT NOT NULL DEFAULT '{}',
        confirmed TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
        bot_id TEXT UNIQUE, bot_username TEXT, db_path TEXT, port INTEGER,
        binding_hash TEXT, binding_expires INTEGER, error TEXT NOT NULL DEFAULT '',
        invitation_used TEXT, activated_at INTEGER);
      CREATE TABLE IF NOT EXISTS invitation(hash TEXT PRIMARY KEY, db_path TEXT UNIQUE NOT NULL,
        expires INTEGER NOT NULL, used_by TEXT);
      CREATE TABLE IF NOT EXISTS hub_inbox(update_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,
        processed INTEGER NOT NULL DEFAULT 0,error TEXT);
      CREATE TABLE IF NOT EXISTS hub_outbox(id INTEGER PRIMARY KEY,recipient TEXT NOT NULL,
        body TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS merchant_access(
        tg_owner TEXT NOT NULL,merchant_id TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL DEFAULT 'owner',created_at INTEGER NOT NULL,
        PRIMARY KEY(tg_owner,merchant_id));
      CREATE TABLE IF NOT EXISTS merchant_session(
        tg_owner TEXT PRIMARY KEY,merchant_id TEXT NOT NULL);
    ''')
    cols={r[1] for r in c.execute('PRAGMA table_info(merchant)')}
    for name,definition in [('manage_hash','TEXT'),('manage_expires','INTEGER'),('suspended','INTEGER NOT NULL DEFAULT 0'),('runtime_status',"TEXT NOT NULL DEFAULT 'stopped'")]:
        if name not in cols:c.execute(f'ALTER TABLE merchant ADD COLUMN {name} {definition}')
    from .merchant_verification import migrate as migrate_verification
    migrate_verification(c)
    c.execute('''INSERT OR IGNORE INTO merchant_access(tg_owner,merchant_id,created_at)
      SELECT owner,id,? FROM merchant''',(int(time.time()),))
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_shop_owner ON merchant(db_path) WHERE db_path IS NOT NULL')
    c.commit()
    return c


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account(c, owner):
    owner=str(owner)
    selected=c.execute('''SELECT m.* FROM merchant_session s
      JOIN merchant_access a ON a.tg_owner=s.tg_owner AND a.merchant_id=s.merchant_id
      JOIN merchant m ON m.id=a.merchant_id WHERE s.tg_owner=?''',(owner,)).fetchone()
    if selected:return selected
    direct=c.execute('SELECT * FROM merchant WHERE owner=?',(owner,)).fetchone()
    if direct:return direct
    return c.execute('''SELECT m.* FROM merchant_access a JOIN merchant m ON m.id=a.merchant_id
      WHERE a.tg_owner=? ORDER BY a.created_at LIMIT 1''',(owner,)).fetchone()


def grant_access(c, owner, merchant_id, role='owner', select=False):
    owner=str(owner)
    c.execute('INSERT OR IGNORE INTO merchant_access(tg_owner,merchant_id,role,created_at) VALUES(?,?,?,?)',
              (owner,merchant_id,role,int(time.time())))
    if select:
        c.execute('INSERT INTO merchant_session(tg_owner,merchant_id) VALUES(?,?) ON CONFLICT(tg_owner) DO UPDATE SET merchant_id=excluded.merchant_id',
                  (owner,merchant_id))


def owner_accounts(c, owner):
    return c.execute('''SELECT m.* FROM merchant_access a JOIN merchant m ON m.id=a.merchant_id
      WHERE a.tg_owner=? ORDER BY a.created_at''',(str(owner),)).fetchall()


def summary(values):
    fields = [('shop_name','档口'),('owner_tg_username','老板 TG'),('owner_wechat','老板微信'),('quote_rules','商家报价规则')]
    lines = [f'{label}：{values.get(k) or "未填写"}' for k,label in fields]
    rules = [values[k] for k,_ in STEPS if k in RULE_KEYS and values.get(k)]
    lines += ['填写的红线：\n' + ('\n'.join('• '+r for r in rules) if rules else '无；所有参考项均未启用')]
    return '\n'.join(lines)


def link(c, m):
    base = os.environ.get('ONBOARDING_PUBLIC_URL', '').rstrip('/')
    if not base.startswith('https://') and not os.environ.get('ONBOARDING_ALLOW_HTTP_TEST'):
        return '资料已保存。密钥接入需要管理员先配置 HTTPS 安全入口；请勿在聊天中发送 Token。配置完成后回复“绑定客服”。'
    token = secrets.token_urlsafe(32)
    c.execute('UPDATE merchant SET binding_hash=?,binding_expires=? WHERE id=?',
              (digest(token),int(time.time())+900,m['id']))
    return ('请打开专属安全页面填写你自己的客服 bot Token（15 分钟有效）：\n'
            +base+'/merchant/connect.html?k='+token+'\n请核对 bot 身份后确认绑定。不要发送 Token 到聊天中。')


def new_invite(c, db_path):
    p = Path(db_path).resolve()
    if not p.is_file():
        raise ValueError('档口数据库不存在')
    if c.execute('SELECT 1 FROM merchant WHERE db_path=?',(str(p),)).fetchone():
        raise ValueError('档口已经认领')
    token = secrets.token_urlsafe(24)
    c.execute('INSERT INTO invitation(hash,db_path,expires) VALUES(?,?,?) ON CONFLICT(db_path) DO UPDATE SET hash=excluded.hash,expires=excluded.expires,used_by=NULL,verified_owner=NULL',
              (digest(token),str(p),int(time.time())+86400))
    c.commit()
    return token


def handle(c, owner, text):
    """Called inside the inbox transaction; only the authenticated private TG sender is used."""
    text = text.strip()
    if text.startswith('/start claim_'):
        text='绑定已有档口 '+text[len('/start claim_'):]
    if text.startswith(('核对联系方式 ', '认证手机号 ')):
        from .ownership_registry import match
        return match(c,owner,text.split(' ',1)[1])
    if text == '我的档口':
        rows=owner_accounts(c,owner)
        if not rows:return '你还没有档口。回复“创建新档口”开始配置。'
        current=account(c,owner)
        return '你的档口：\n'+'\n'.join(
            ('👉 ' if current and row['id']==current['id'] else '　 ')+
            f"{row['id'][:8]}｜{json.loads(row['confirmed'] if row['confirmed']!='{}' else row['draft']).get('shop_name') or '未命名档口'}｜{row['state']}/{row['runtime_status']}"
            for row in rows)+'\n回复“切换档口 前8位编号”进入对应档口。'
    if text.startswith('切换档口 '):
        key=text.split(' ',1)[1].strip()
        choices=[row for row in owner_accounts(c,owner) if row['id'].startswith(key)]
        if len(choices)!=1:return '档口编号无效或不唯一。回复“我的档口”查看编号。'
        grant_access(c,owner,choices[0]['id'],select=True)
        values=json.loads(choices[0]['confirmed'] if choices[0]['confirmed']!='{}' else choices[0]['draft'])
        return f"已切换到：{values.get('shop_name') or '未命名档口'}。回复“查看配置”“商品管理”或“认证进度”。"
    m = account(c,owner)
    if m and m['invitation_used']:
        from .merchant_verification import verified,request as request_verification
        if not verified(c,m):
            if text=='暂停客服':
                c.execute("UPDATE merchant SET state='paused',binding_hash=NULL,manage_hash=NULL WHERE id=?",(m['id'],))
            return request_verification(c,owner,m['invitation_used'])
    if m and m['invitation_used'] and text=='认证进度':return '档口认领认证已通过，可回复“继续配置”或“查看配置”。'
    if text in ('/start','/help','我是商家','接入客服'):
        return ('你好，这里是商家接入助手。\n已有档口回复“绑定已有档口 邀请码”；新商家回复“创建新档口”。\n'
                '商家自己通过 @BotFather 的 /newbot 创建客服 bot，我会引导你配置资料与规则。未填写的红线不生效。\n'
                +('你已有配置，回复“我的档口”“继续配置”“查看配置”“绑定客服”“商品管理”“启用客服”或“暂停客服”。' if m else '客户采购咨询请使用商家提供的客服 bot。'))
    if not m and text=='认证进度':
        row=c.execute('SELECT * FROM ownership_request WHERE owner=? ORDER BY created_at DESC LIMIT 1',(str(owner),)).fetchone()
        if not row:return '暂无认领认证申请，请使用管理员提供的档口认领链接提交。'
        if row['status']=='approved':
            invitation=c.execute('SELECT * FROM invitation WHERE hash=?',(row['invitation_hash'],)).fetchone()
            if not invitation or invitation['expires']<=time.time() or invitation['used_by']:return '认领链接已失效，请联系管理员重新核验签发。'
            return _claim_verified(c,owner,invitation)
        return '认证进度：'+('待管理员核验，请通过档口原有登记联系方式联系管理员。' if row['status']=='pending' else '未通过，请联系管理员。')
    if not m:
        db_path, invitation = None, None
        if text.startswith('绑定已有档口 '):
            invitation = digest(text.split(' ',1)[1].strip())
            row=c.execute('SELECT * FROM invitation WHERE hash=? AND used_by IS NULL AND expires>?',(invitation,int(time.time()))).fetchone()
            if not row:return '邀请码无效、已使用或已过期，请联系档口管理员。不能仅凭档口名称认领。'
            from .merchant_verification import verified as grant_verified
            if not grant_verified(c,{'invitation_used':invitation,'owner':str(owner)}):
                from .merchant_verification import request as request_verification
                return request_verification(c,owner,invitation)
            db_path=row['db_path']
        elif text != '创建新档口':
            return '请先回复“我是商家”，选择创建新档口或使用邀请码绑定已有档口。'
        if c.execute('SELECT count(*) FROM merchant').fetchone()[0] >= int(os.environ.get('MERCHANT_MAX_ACCOUNTS','100')):
            return '当前接入名额已满，请联系管理员。'
        mid=secrets.token_hex(12)
        draft={}
        if db_path:
            source=sqlite3.connect(Path(db_path).as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
            p=dict(source.execute('SELECT * FROM shop_profile WHERE id=1').fetchone())
            draft={k:p.get(k,'') for k in ('shop_name','owner_tg_username','owner_wechat')}
            from .merchant_policy import read
            previous=read(source)
            if previous:draft.update(previous)
            else:
                from .cs import DEFAULT_STORE_REDLINE
                rule=source.execute("SELECT text_raw FROM cs_redline WHERE product_id='' ").fetchone()
                if rule and rule[0] != DEFAULT_STORE_REDLINE:draft['other']=rule[0]
            source.close()
        c.execute('INSERT INTO merchant(id,owner,db_path,draft,invitation_used) VALUES(?,?,?,?,?)',
                  (mid,str(owner),db_path,json.dumps(draft,ensure_ascii=False),invitation))
        grant_access(c,owner,mid,select=True)
        if invitation:c.execute('UPDATE invitation SET used_by=? WHERE hash=?',(str(owner),invitation))
        return STEPS[0][1]+'\n'+('已有资料将展示在确认摘要中；填写新值会覆盖，跳过则保留。' if draft else '')
    if text in ('查看配置','查看进度'):
        return f'状态：{m["state"]} / {m["runtime_status"]}\n已确认配置：\n'+(summary(json.loads(m['confirmed'])) if m['confirmed']!='{}' else '尚未确认')+'\n草稿：\n'+summary(json.loads(m['draft']))+'\n'+(m['error'] or '')
    if text == '商品管理':
        if m['runtime_status']!='running' or m['state'] not in ('enabled','catalog_ready'):return '商品服务尚未运行；请完成配置，或联系管理员检查测试档口。'
        base=os.environ.get('ONBOARDING_PUBLIC_URL','').rstrip('/')
        if not base.startswith('https://'):return '商品管理需要 HTTPS 入口，请联系管理员。'
        token=secrets.token_urlsafe(32)
        c.execute('UPDATE merchant SET manage_hash=?,manage_expires=? WHERE id=?',(digest(token),int(time.time())+3600,m['id']))
        return '你的档口商品管理（1小时有效，请勿转发）：\n'+base+'/merchant/manage/'+m['id']+'/?t='+token+'\n可新增、修改商品及审批导入；客户仅能查询你开启“可观测”的商品。'
    if text == '暂停客服':
        c.execute("UPDATE merchant SET state='paused',binding_hash=NULL WHERE id=?",(m['id'],))
        return '已请求暂停客服。配置与客户数据保留，回复“启用客服”恢复。'
    if text == '启用客服':
        if not m['bot_id'] or m['confirmed']=='{}':return '请先完成资料确认和“绑定客服”，再启用。'
        enabled=c.execute("SELECT count(*) FROM merchant WHERE state='enabled' AND id!=?",(m['id'],)).fetchone()[0]
        if enabled>=int(os.environ.get('MERCHANT_MAX_ACTIVE','5')):return '当前运行名额已满，请联系管理员。'
        c.execute("UPDATE merchant SET state='enabled',error='' WHERE id=?",(m['id'],))
        return f'已请求启动客服，回复“查看进度”检查运行结果。客户入口：https://t.me/{m["bot_username"]}'
    if text == '绑定客服':
        if m['confirmed']=='{}':return '请先完成资料配置并回复“确认配置”。'
        return link(c,m)
    if text == '确认配置':
        if m['step'] < len(STEPS):return '配置尚未走完，回复“继续配置”；不需要的项目可以跳过。'
        values=json.loads(m['draft'])
        if not values.get('shop_name'):return '档口名称不能为空，回复“修改配置”重新填写。'
        if any(values.get(k) for k in RULE_KEYS) and not (values.get('owner_tg_username') or values.get('owner_wechat')):
            return '你设置了转人工条件，但还没有老板联系方式。请回复“修改配置”补充至少一种联系方式。'
        c.execute("UPDATE merchant SET confirmed=draft,revision=revision+1,state=CASE WHEN state='enabled' THEN state ELSE 'ready' END WHERE id=?",(m['id'],))
        return '配置已确认。'+(('运行中的客服会自动更新。' if m['state']=='enabled' else '配置已保存，回复“启用客服”启动。') if m['bot_id'] else '下一步回复“绑定客服”；你也可以先通过 @BotFather 创建自己的 bot。')
    if text == '修改配置':
        c.execute('UPDATE merchant SET step=0,suspended=0 WHERE id=?',(m['id'],));return STEPS[0][1]+' 可回复“保持不变”。'
    if text in ('稍后继续','取消配置'):
        c.execute('UPDATE merchant SET suspended=1 WHERE id=?',(m['id'],))
        return '草稿已保留，当前生效配置不变。回复“继续配置”恢复。'
    step=m['step']
    if text == '上一步':
        step=max(0,step-1);c.execute('UPDATE merchant SET step=? WHERE id=?',(step,m['id']));return STEPS[step][1]
    if text == '继续配置':
        c.execute('UPDATE merchant SET suspended=0 WHERE id=?',(m['id'],))
        return STEPS[step][1] if step<len(STEPS) else summary(json.loads(m['draft']))+'\n回复“确认配置”生效，或“修改配置”。'
    if m['suspended']:return '配置已暂停，回复“继续配置”恢复。'
    if step>=len(STEPS):return '请回复“查看配置”“确认配置”“修改配置”“绑定客服”“商品管理”“启用客服”或“暂停客服”。'
    key,_=STEPS[step];values=json.loads(m['draft'])
    if text not in ('跳过','保持不变'):
        if len(text)>2000:return '这一项请控制在 2000 字以内。'
        if key=='shop_name' and text=='清空':return '档口名称不能为空。'
        if key in ('owner_tg_username','owner_wechat') and text!='清空':
            from .cs import validate_shop
            try:text=validate_shop({key:text})[key]
            except ValueError:return '联系方式格式无效，请重新填写，或回复“跳过”。'
        values[key]='' if text=='清空' else text
    if key=='shop_name' and not values.get(key):return '请填写档口名称，这一项不能跳过。'
    step+=1;c.execute('UPDATE merchant SET step=?,draft=? WHERE id=?',(step,json.dumps(values,ensure_ascii=False),m['id']))
    return STEPS[step][1] if step<len(STEPS) else summary(values)+'\n以上只包含你填写的内容。回复“确认配置”生效；“修改配置”可重填。'


def _claim_verified(c,owner,invitation):
    # Resume after approval using a temporary synthetic bearer that has the same
    # transaction-scoped authorization; the original link is invalidated atomically.
    token=secrets.token_urlsafe(24)
    old_hash=invitation['hash'];new_hash=digest(token)
    c.execute('UPDATE invitation SET hash=? WHERE hash=? AND verified_owner=?',(new_hash,old_hash,str(owner)))
    c.execute('UPDATE ownership_request SET invitation_hash=? WHERE invitation_hash=?',(new_hash,old_hash))
    return handle(c,owner,'绑定已有档口 '+token)
