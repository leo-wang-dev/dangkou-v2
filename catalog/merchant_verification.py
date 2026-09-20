"""Existing-shop ownership is granted only after independent verification.
A phone number, username or forwarded invitation alone is never ownership proof.
"""
import json
import secrets
import time
from . import merchant_onboarding as hub


def migrate(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS ownership_request(
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, invitation_hash TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
      reviewed_at INTEGER, review_note TEXT, UNIQUE(owner,invitation_hash));
      CREATE TABLE IF NOT EXISTS ownership_audit(
      id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, decision TEXT NOT NULL,
      reviewer TEXT NOT NULL, evidence_reference TEXT NOT NULL, created_at INTEGER NOT NULL);
    ''')
    columns={r[1] for r in c.execute('PRAGMA table_info(invitation)')}
    if 'verified_owner' not in columns:c.execute('ALTER TABLE invitation ADD COLUMN verified_owner TEXT')
    from .ownership_registry import migrate as migrate_registry
    migrate_registry(c)


def request(c,owner,invitation):
    row=c.execute('SELECT * FROM ownership_request WHERE owner=? AND invitation_hash=?',(str(owner),invitation)).fetchone()
    if not row:
        rid=secrets.token_hex(10)
        c.execute('INSERT INTO ownership_request(id,owner,invitation_hash,created_at) VALUES(?,?,?,?)',(rid,str(owner),invitation,int(time.time())))
    else:
        rid=row['id']
        if row['status']=='rejected':return '此认领申请未通过认证。请联系管理员核实档口原有登记信息，不能通过重新填写联系方式取得权限。'
    return '认领申请已提交，编号：'+rid+'。\n管理员会通过档口原有登记联系方式核实本人身份。核验通过前不能查看档口资料、绑定 bot 或管理商品。邀请码和自行填写的手机号都不能单独证明档口归属。\n稍后回复“认证进度”查看。'


def review(c,rid,approved,reviewer,evidence,holder_confirmed=False,contact_revision=None):
    if not reviewer.strip() or len(evidence.strip())<8:
        raise ValueError('必须记录审核人及通过档口原有登记联系方式核验的凭据编号，不能仅凭申请人自报手机号批准')
    c.execute('BEGIN IMMEDIATE')
    try:
        row=c.execute('SELECT * FROM ownership_request WHERE id=?',(rid,)).fetchone()
        if not row or row['status']!='pending':raise ValueError('申请不存在或已处理')
        invitation=c.execute('SELECT * FROM invitation WHERE hash=?',(row['invitation_hash'],)).fetchone()
        if not invitation or (invitation['used_by'] and invitation['used_by']!=row['owner']) or (not invitation['used_by'] and invitation['expires']<=time.time()):raise ValueError('邀请码已失效，请重新签发并核验')
        if approved:
            from .ownership_registry import contact
            historical=contact(c,rid)
            if not historical:raise ValueError('缺少原有登记联系方式，不能批准')
            if contact_revision != historical['revision']:raise ValueError('底册版本变化，请重新核实')
            if row['contact_revision'] != historical['revision']:raise ValueError('申请人尚未完成原登记联系方式匹配')
            if not holder_confirmed:raise ValueError('须先通过原登记联系方式确认账号持有人，号码匹配不等于身份认证')
            if invitation['verified_owner'] and invitation['verified_owner']!=row['owner']:raise ValueError('此档口已核验其他申请人')
            c.execute('UPDATE invitation SET verified_owner=?,expires=MAX(expires,?) WHERE hash=?',(row['owner'],int(time.time())+86400,row['invitation_hash']))
        state='approved' if approved else 'rejected'
        c.execute('UPDATE ownership_request SET status=?,reviewed_at=?,review_note=? WHERE id=?',(state,int(time.time()),evidence,rid))
        c.execute('INSERT INTO ownership_audit(request_id,decision,reviewer,evidence_reference,created_at) VALUES(?,?,?,?,?)',(rid,state,reviewer,evidence,int(time.time())))
        c.commit()
    except BaseException:c.rollback();raise


def register(app,secure):
    from fastapi import HTTPException, Request
    from pydantic import BaseModel,Field
    from .api import _auth
    def admin_auth(request):
        import os,secrets
        supplied=request.headers.get('X-Service-Token','')
        scoped=os.environ.get('MERCHANT_REVIEW_TOKEN','')
        expires=int(os.environ.get('MERCHANT_REVIEW_TOKEN_EXPIRES','0'))
        if scoped and supplied and secrets.compare_digest(scoped,supplied) and time.time()<expires:return
        _auth(request,app.state.token)
    class Review(BaseModel):
        approved:bool
        reviewer:str=Field(min_length=1,max_length=100)
        evidence_reference:str=Field(min_length=8,max_length=500)
        holder_confirmed:bool=False
        contact_revision:int|None=None
    class ContactIn(BaseModel):
        kind:str
        value:str=Field(max_length=100)
        source_reference:str=Field(min_length=8,max_length=500)
        recorded_by:str=Field(min_length=1,max_length=100)
    @app.put('/merchant/ownership-requests/{rid}/contact')
    def put_contact(rid:str,body:ContactIn,request:Request):
        secure(request);admin_auth(request)
        from .ownership_registry import save
        c=hub.connect()
        try:return {'revision':save(c,rid,body.kind,body.value,body.source_reference,body.recorded_by)}
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        finally:c.close()
    @app.get('/merchant/ownership-requests')
    def pending(request:Request):
        secure(request);admin_auth(request)
        c=hub.connect()
        try:
            from .ownership_registry import contact
            items=[]
            for row in c.execute('SELECT id,owner,status,created_at,contact_revision,contact_attempts FROM ownership_request ORDER BY created_at DESC LIMIT 100'):
                record=contact(c,row['id'])
                # Show which shop is being claimed, using its existing record, not applicant drafts.
                import sqlite3
                from pathlib import Path
                target=c.execute('SELECT i.db_path FROM invitation i JOIN ownership_request r ON r.invitation_hash=i.hash WHERE r.id=?',(row['id'],)).fetchone()
                shop={}
                if target:
                    source=sqlite3.connect(Path(target[0]).as_uri()+'?mode=ro',uri=True)
                    source.row_factory=sqlite3.Row
                    try:
                        profile=source.execute('SELECT shop_name,shop_id,stall_no FROM shop_profile WHERE id=1').fetchone()
                        if profile:shop=dict(profile)
                    finally:source.close()
                items.append({**dict(row),'shop':shop,'historical_contact':dict(record) if record else None})
            return {'requests':items}
        finally:c.close()
    @app.post('/merchant/ownership-requests/{rid}/review')
    def decide(rid:str,body:Review,request:Request):
        secure(request);admin_auth(request)
        c=hub.connect()
        try:
            review(c,rid,body.approved,body.reviewer,body.evidence_reference,body.holder_confirmed,body.contact_revision)
            return {'reviewed':True}
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        finally:c.close()


def verified(c,m):
    if not m['invitation_used']:return True  # New empty shop grants no existing shop access.
    row=c.execute('''SELECT i.verified_owner FROM invitation i
      JOIN ownership_contact ct ON ct.db_path=i.db_path
      JOIN ownership_request r ON r.invitation_hash=i.hash AND r.owner=i.verified_owner
      WHERE i.hash=? AND r.status='approved' AND r.contact_revision=ct.revision''',(m['invitation_used'],)).fetchone()
    return bool(row and row['verified_owner']==m['owner'])
