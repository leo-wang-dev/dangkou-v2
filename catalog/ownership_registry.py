"""Admin-maintained historical contacts; never populated from merchant setup drafts."""
import re
import time


def migrate(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS ownership_contact(
      db_path TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL,
      source_reference TEXT NOT NULL, recorded_by TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS ownership_contact_audit(
      id INTEGER PRIMARY KEY, db_path TEXT NOT NULL, kind TEXT NOT NULL,
      revision INTEGER NOT NULL, source_reference TEXT NOT NULL,
      recorded_by TEXT NOT NULL, created_at INTEGER NOT NULL);
    ''')
    cols={r[1] for r in c.execute('PRAGMA table_info(ownership_request)')}
    for key,kind in [('contact_revision','INTEGER'),('contact_attempts','INTEGER NOT NULL DEFAULT 0')]:
        if key not in cols:c.execute(f'ALTER TABLE ownership_request ADD COLUMN {key} {kind}')

    c.execute("""UPDATE ownership_request SET status='pending',reviewed_at=NULL
      WHERE status='approved' AND NOT EXISTS (
        SELECT 1 FROM invitation i JOIN ownership_contact ct ON ct.db_path=i.db_path
        WHERE i.hash=ownership_request.invitation_hash AND ct.revision=ownership_request.contact_revision)
      """)


def normalize(kind,value):
    value=value.strip()
    if kind=='phone':
        value=re.sub(r'[\s()-]','',value)
        if re.fullmatch(r'1[3-9]\d{9}',value):value='+86'+value
        if not re.fullmatch(r'\+[1-9]\d{7,14}',value):raise ValueError('请输入含国家区号的手机号，例如 +86138…')
    elif kind=='wechat':
        if not re.fullmatch(r'[A-Za-z0-9_+@.-]{3,100}',value):raise ValueError('微信号格式无效')
    else:raise ValueError('仅支持原登记手机号或微信号')
    return value


def contact(c,rid):
    return c.execute('SELECT ct.* FROM ownership_contact ct JOIN invitation i ON i.db_path=ct.db_path JOIN ownership_request r ON r.invitation_hash=i.hash WHERE r.id=?',(rid,)).fetchone()


def save(c,rid,kind,value,source,operator):
    value=normalize(kind,value)
    if len(source.strip())<8 or not operator.strip():raise ValueError('必须注明原始登记资料来源和登记人；不能使用申请人本次自报的联系方式')
    row=c.execute('SELECT i.db_path FROM invitation i JOIN ownership_request r ON r.invitation_hash=i.hash WHERE r.id=? AND r.status=?',(rid,'pending')).fetchone()
    if not row:raise ValueError('申请不存在或不再待审核')
    path=row[0];now=int(time.time())
    c.execute('INSERT INTO ownership_contact(db_path,kind,value,source_reference,recorded_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(db_path) DO UPDATE SET kind=excluded.kind,value=excluded.value,source_reference=excluded.source_reference,recorded_by=excluded.recorded_by,revision=ownership_contact.revision+1,updated_at=excluded.updated_at',(path,kind,value,source,operator,now))
    current=contact(c,rid)
    c.execute('INSERT INTO ownership_contact_audit(db_path,kind,revision,source_reference,recorded_by,created_at) VALUES(?,?,?,?,?,?)',(path,kind,current['revision'],source,operator,now))
    # A changed historical contact invalidates previous matches and approvals.
    c.execute('UPDATE ownership_request SET contact_revision=NULL,contact_attempts=0 WHERE invitation_hash IN (SELECT hash FROM invitation WHERE db_path=?)',(path,))
    c.execute("UPDATE ownership_request SET status='pending',reviewed_at=NULL WHERE status='approved' AND invitation_hash IN (SELECT hash FROM invitation WHERE db_path=?)",(path,))
    c.execute('UPDATE invitation SET verified_owner=NULL WHERE db_path=?',(path,))
    c.execute("UPDATE merchant SET state=CASE WHEN state='enabled' THEN 'paused' ELSE state END,binding_hash=NULL,manage_hash=NULL WHERE db_path=?",(path,))
    c.commit()
    return current['revision']


def match(c,owner,value):
    row=c.execute("SELECT * FROM ownership_request WHERE owner=? AND status='pending' ORDER BY created_at DESC LIMIT 1",(str(owner),)).fetchone()
    if not row:return '暂无待核验的认领申请。请先提交档口认领申请。'
    record=contact(c,row['id'])
    if not record:return '管理员尚未录入原始登记资料。请等待人工核验，不会使用你新填的号码作为底册。'
    if row['contact_attempts']>=5:return '核对次数已达上限，请联系管理员重新核验。'
    c.execute('UPDATE ownership_request SET contact_attempts=contact_attempts+1 WHERE id=?',(row['id'],))
    try:matched=normalize(record['kind'],value)==record['value']
    except ValueError:matched=False
    if matched:
        c.execute('UPDATE ownership_request SET contact_revision=? WHERE id=?',(record['revision'],row['id']))
        return '登记资料匹配。尚未授予权限：管理员还需通过原登记电话或微信确认持有人，确认后你可以回复“认证进度”。'
    c.execute('UPDATE ownership_request SET contact_revision=NULL WHERE id=?',(row['id'],))
    return '未完成资料匹配，请检查原登记联系方式，或联系管理员核验。'
