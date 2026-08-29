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
    images: list[str] | None = None


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

    @app.post('/upload')
    async def upload(request: Request):
        """图片上传 → 暂存 data/images/_upload/（审批换图/商品换图用）。"""
        _auth(request, app.state.token)
        import uuid
        from fastapi import UploadFile, File
        form = await request.form()
        up: UploadFile = form['file']
        data = await up.read()
        if not data:
            raise HTTPException(400, 'empty file')
        ext = os.path.splitext(up.filename or '')[1] or '.png'
        if ext.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'):
            raise HTTPException(400, f'不支持的格式: {ext}')
        rel = app.state.storage.save('_upload', uuid.uuid4().hex[:12], ext.lstrip('.'), data)
        return {'path': rel}

    # ---- 导入 ----
    @app.post('/import')
    def do_import(body: ImportIn, request: Request):
        _auth(request, app.state.token)
        if not os.path.isfile(body.path):
            raise HTTPException(404, f'文件不存在: {body.path}')
        est = min(1800, max(120, int(os.path.getsize(body.path) / 1048576 * 30)))
        return {'doc_id': ingest.start(app.state.conn, app.state.storage,
                                       body.path, body.category,
                                       callback=app.state.callback),
                'est_sec': est}

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

    @app.get('/tickets/{ticket_id}')
    def ticket_detail(ticket_id: int):
        import json as _json
        r = app.state.conn.execute(
            'SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
        if r is None:
            raise HTTPException(404, 'no such ticket')
        payload = _json.loads(r['payload'])
        # 行级决策钥匙：new 行=_rid，update/delist 行=商品 id；顺带补图片预览地址
        wd = payload.get('work_dir')
        for d in payload.get('drafts', {}).get('new', []):
            d.setdefault('_rid', None)
            fns = [f for f in (d.get('images') or []) if f] or \
                  ([d['image_main']] if d.get('image_main') else [])
            if wd and fns:
                d['_imgs'] = [f"/ticketimg/{ticket_id}/{fn}?token={app.state.token}" for fn in fns]
                d['_img'] = d['_imgs'][0]
        return {'ticket': {'id': r['id'], 'ticket_type': r['ticket_type'],
                           'category': r['category'], 'status': r['status'],
                           'created_at': r['created_at']},
                'payload': payload}

    @app.get('/ticketimg/{ticket_id}/{fname}')
    def ticket_img(ticket_id: int, fname: str, request: Request):
        import json as _json
        import os as _os
        _auth(request, app.state.token)
        r = app.state.conn.execute(
            'SELECT payload FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
        if r is None:
            raise HTTPException(404, 'no such ticket')
        wd = (_json.loads(r['payload']) or {}).get('work_dir')
        if not wd:
            raise HTTPException(404, 'no preview')
        safe = _os.path.basename(fname)  # 防目录穿越：只取文件名
        p = _os.path.join(wd, safe)
        if not _os.path.isfile(p):
            raise HTTPException(404, 'no image')
        data = open(p, 'rb').read()
        media = 'image/png'
        if safe.lower().endswith(('.tif', '.tiff', '.bmp')):
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert('RGB')
            buf = io.BytesIO(); img.save(buf, format='PNG'); data = buf.getvalue()
        elif safe.lower().endswith(('.jpg', '.jpeg')):
            media = 'image/jpeg'
        return Response(content=data, media_type=media)

    @app.post('/tickets/{ticket_id}/decision')
    def decide_ticket(ticket_id: int, body: DecisionIn):
        try:
            result = tickets.decide(app.state.conn, ticket_id, body.token,
                                    body.approved, body.decisions)
        except tickets.TicketError as e:
            raise HTTPException(400, str(e))
        if body.approved and result.get('created_rows'):
            _persist_images_and_reindex(result)
        if body.approved and result.get('images_applied'):
            _apply_product_images(result['images_applied'])
        return result

    def _apply_product_images(info):
        """商品换图（mutate 审批通过）：暂存图落位产品目录 → 更新主图/图集 → 重嵌入。"""
        from . import search
        import io

        def _webpify(data, fname):
            if fname.lower().endswith(('.tif', '.tiff', '.bmp')):
                from PIL import Image
                img = Image.open(io.BytesIO(data)).convert('RGB')
                buf = io.BytesIO(); img.save(buf, format='PNG')
                return buf.getvalue(), '.png'
            return data, os.path.splitext(fname)[1] or '.png'

        pid, table = info['id'], info['table']
        cat = next(k for k, v in TEMPLATES.items() if v.table == table)
        rels = []
        for i, fn in enumerate(info['images']):
            src = os.path.join(app.state.storage.base, str(fn))
            if not os.path.exists(src):
                continue
            data, ext = _webpify(open(src, 'rb').read(), fn)
            rels.append(app.state.storage.save(cat, pid, f'img{i}{ext}', data))
        if not rels:
            return
        app.state.conn.execute(
            f"UPDATE {table} SET image_main=?, images=?, updated_at=datetime('now') WHERE id=?",
            (rels[0], __import__('json').dumps(rels, ensure_ascii=False), pid))
        app.state.conn.execute('DELETE FROM embedding WHERE product_id=?', (pid,))
        app.state.conn.commit()
        search.reindex(app.state.conn, app.state.storage, cat)

    def _persist_images_and_reindex(result):
        """导入审批通过：Agent 工作目录的全部图片落位 storage（tif/bmp转png）→ 更新主图/图集 → 向量入池。"""
        from . import search
        import io

        def _webpify(data: bytes, fname: str) -> tuple[bytes, str]:
            if fname.lower().endswith(('.tif', '.tiff', '.bmp')):
                from PIL import Image
                img = Image.open(io.BytesIO(data)).convert('RGB')
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                return buf.getvalue(), '.png'
            return data, os.path.splitext(fname)[1] or '.png'

        wd = result.get('work_dir')
        cats = set()
        for row in result.get('created_rows', []):
            fn_list = row.get('images') or ([row['image_main']] if row.get('image_main') else [])
            if not wd or not fn_list:
                continue
            rels = []
            for i, fn in enumerate(fn_list):
                fn = str(fn)
                src = (os.path.join(app.state.storage.base, fn)
                       if fn.startswith('_upload/')
                       else os.path.join(wd, fn))
                if not os.path.exists(src):
                    continue
                data, ext = _webpify(open(src, 'rb').read(), fn)
                rels.append(app.state.storage.save(row['_category'], row['id'],
                                                   f'img{i}{ext}', data))
            if rels:
                app.state.conn.execute(
                    f"UPDATE {row['_table']} SET image_main=?, images=? WHERE id=?",
                    (rels[0], __import__('json').dumps(rels, ensure_ascii=False), row['id']))
            cats.add(row['_category'])
        app.state.conn.commit()
        for cat in cats:
            search.reindex(app.state.conn, app.state.storage, cat)

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
        if not body.changes and not body.images:
            raise HTTPException(400, 'changes 与 images 至少给一个')
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'update', 'product_id': pid,
                             'changes': body.changes or {},
                             **({'images': body.images} if body.images else {}),
                             'before': _current(category, pid)})
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

    # ---- 检索 ----
    class SearchIn(BaseModel):
        image_path: str
        top_k: int = 5
        exclude_ids: list[str] = []

    @app.post('/search')
    def do_search(body: SearchIn, request: Request):
        _auth(request, app.state.token)
        from . import search
        if not os.path.isfile(body.image_path):
            raise HTTPException(404, '图片不存在')
        vec = search.embed_image(open(body.image_path, 'rb').read())
        return {'hits': search.query(app.state.conn, vec,
                                     top_k=body.top_k, exclude=body.exclude_ids)}

    # ---- 报价单 ----
    class QuoteIn(BaseModel):
        category: str
        product_ids: list[str]

    @app.post('/quote')
    def do_quote(body: QuoteIn, request: Request):
        _auth(request, app.state.token)
        import uuid
        from . import quote as quote_mod
        out_dir = os.path.join(app.state.storage.base, '_quotes')
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f'quote-{uuid.uuid4().hex[:8]}.xlsx')
        return {'path': quote_mod.generate(app.state.conn, app.state.storage,
                                           body.category, body.product_ids, out)}
