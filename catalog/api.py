import os

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from . import ingest, tickets
from .templates import TEMPLATES, row_to_dict


def _auth(request: Request, token: str):
    if not token:
        return
    h = request.headers.get('X-Service-Token')
    if h != token and request.query_params.get('token') != token:
        raise HTTPException(401, 'unauthorized')


class ImportIn(BaseModel):
    path: str
    category: str


class DecisionIn(BaseModel):
    token: str
    approved: bool
    decisions: dict | None = None


class MutateIn(BaseModel):
    changes: dict
    product_id: str | None = None


def register_routes(app: FastAPI):
    @app.get('/health')
    def health():
        return {'status': 'ready', 'version': 'v2.0'}

    @app.get('/img/{rel:path}')
    def img(rel: str, request: Request):
        _auth(request, app.state.token)
        try:
            data = app.state.storage.read(rel)
        except FileNotFoundError:
            raise HTTPException(404, 'no image')
        return Response(content=data, media_type='image/png')

    # ---- 导入 ----
    @app.post('/import')
    def do_import(body: ImportIn, request: Request):
        _auth(request, app.state.token)
        if not os.path.isfile(body.path):
            raise HTTPException(404, f'文件不存在: {body.path}')
        return {'doc_id': ingest.start(app.state.conn, app.state.storage,
                                       body.path, body.category,
                                       callback=app.state.callback)}

    @app.get('/import/{doc_id}')
    def import_status(doc_id: int):
        try:
            return ingest.status(app.state.conn, doc_id)
        except KeyError:
            raise HTTPException(404, 'no such doc')

    # ---- 工单 ----
    @app.get('/tickets')
    def list_tickets():
        rows = app.state.conn.execute(
            'SELECT id, ticket_type, category, status, token, created_at '
            'FROM approval_ticket ORDER BY id DESC').fetchall()
        # token 只对 pending 暴露（审批链接要带它；已决工单不回）
        return {'tickets': [
            {**dict(r), 'token': (r['token'] if r['status'] == 'pending' else None)}
            for r in rows]}

    @app.post('/tickets/{ticket_id}/decision')
    def decide_ticket(ticket_id: int, body: DecisionIn):
        try:
            result = tickets.decide(app.state.conn, ticket_id, body.token,
                                    body.approved, body.decisions)
        except tickets.TicketError as e:
            raise HTTPException(400, str(e))
        return result

    # ---- 商品（读直查；写=审批工单）----
    @app.get('/products/{category}')
    def list_products(category: str):
        if category not in TEMPLATES:
            raise HTTPException(404, '未知品类')
        t = TEMPLATES[category]
        rows = app.state.conn.execute(
            f'SELECT * FROM {t.table} ORDER BY id').fetchall()
        return {'template': {'key': t.key, 'name': t.name,
                             'fields': [{'col': c, 'label': l} for c, l in t.fields]},
                'products': [row_to_dict(t, r) for r in rows]}

    @app.post('/products/{category}')
    def create_product(category: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            raise HTTPException(404, '未知品类')
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'create',
                             'product_id': None, 'changes': body.changes})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.patch('/products/{category}/{pid}')
    def update_product(category: str, pid: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'update', 'product_id': pid,
                             'changes': body.changes, 'before': _current(category, pid)})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.delete('/products/{category}/{pid}')
    def delete_product(category: str, pid: str, request: Request):
        _auth(request, app.state.token)
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'delete', 'product_id': pid})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    def _current(category, pid):
        t = TEMPLATES[category]
        r = app.state.conn.execute(
            f'SELECT * FROM {t.table} WHERE id=?', (pid,)).fetchone()
        return row_to_dict(t, r) if r else None
