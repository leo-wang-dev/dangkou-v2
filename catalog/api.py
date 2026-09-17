import json
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


def _push_redline_card(ticket_id, token, product_id, old_text, new_text):
    """红线审批卡推微信（本地未配 NOTIFY_TOKEN 时静默跳过，E2E 用落盘验证）。"""
    import requests
    url = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify')
    ntoken = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
    if not ntoken:
        print(f'[cs-redline] 审批卡(未配微信通道，仅日志)：旧「{old_text[:30]}」→'
              f'新「{new_text[:30]}」', flush=True)
        return
    base = os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')
    scope = f'商品 {product_id}' if product_id else '全店'
    link = f'{base}/cs/redline.html?i={ticket_id}&t={token}'
    try:
        requests.post(url, json={'text': (
            f'⚠️ 红线修改审批（{scope}）\n旧：{old_text}\n新：{new_text}\n'
            f'点开确认（批准即生效）：{link}')}, timeout=15,
            headers={'Authorization': f'Bearer {ntoken}'})
    except Exception as e:  # noqa: BLE001
        print(f'[cs-redline] 审批卡推送失败: {e}', flush=True)


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
            if fns:
                d['_imgs'] = [
                    f"/img/{fn}?token={app.state.token}" if fn.startswith('_upload/')
                    else f"/ticketimg/{ticket_id}/{fn}?token={app.state.token}"
                    for fn in fns]
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

    class RowDecisionIn(BaseModel):
        token: str
        row_key: str
        approved: bool
        edits: dict | None = None

    @app.post('/tickets/{ticket_id}/row')
    def decide_row(ticket_id: int, body: RowDecisionIn):
        try:
            result = tickets.decide_row(app.state.conn, ticket_id,
                                        body.token, body.row_key,
                                        body.approved, body.edits)
        except tickets.TicketError as e:
            raise HTTPException(400, str(e))
        if body.approved and result.get('created_rows'):
            _persist_images_and_reindex(result)
        return result

    class DraftEditIn(BaseModel):
        token: str
        row_key: str
        edits: dict

    @app.patch('/tickets/{ticket_id}/draft')
    def save_draft(ticket_id: int, body: DraftEditIn):
        try:
            return tickets.save_draft_edit(app.state.conn, ticket_id,
                                           body.token, body.row_key, body.edits)
        except tickets.TicketError as e:
            raise HTTPException(400, str(e))

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
        norm, to_remark = tickets.normalize_changes(TEMPLATES[category], body.changes or {})
        if not set(body.changes or {}) - set(to_remark) and not body.images:
            _raise_unknown_fields(category)   # 一个合法字段都没有=AI没做映射，打回让它重发
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'create',
                             'product_id': None, 'changes': norm,
                             **({'images': body.images} if body.images else {})})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.patch('/products/{category}/{pid}')
    def update_product(category: str, pid: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if not body.changes and not body.images:
            raise HTTPException(400, 'changes 与 images 至少给一个')
        norm, to_remark = tickets.normalize_changes(TEMPLATES[category], body.changes or {})
        if not set(body.changes or {}) - set(to_remark) and not body.images:
            _raise_unknown_fields(category)
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'update', 'product_id': pid,
                             'changes': norm,
                             **({'images': body.images} if body.images else {}),
                             'before': _current(category, pid)})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.delete('/products/{category}/{pid}')
    def delete_product(category: str, pid: str, request: Request):
        _auth(request, app.state.token)
        tk = tickets.create(app.state.conn, 'mutate', category,
                            {'kind': 'mutate', 'action': 'delete', 'product_id': pid,
                             'before': _current(category, pid)})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    def _raise_unknown_fields(category):
        t = TEMPLATES[category]
        raise HTTPException(
            400, 'changes 里没有可识别的字段。该品类合法字段：'
            + '、'.join(label for _, label in t.fields)
            + '；清单外的信息请拼进"备注"（如"工作温度：160-220℃｜认证：CE"）')

    def _current(category, pid):
        t = TEMPLATES[category]
        r = app.state.conn.execute(
            f'SELECT * FROM {t.table} WHERE id=?', (pid,)).fetchone()
        return row_to_dict(t, r) if r else None

    # ---- 统计查询 ----
    @app.get('/stats')
    def stats(full: bool = False, category: str | None = None):
        """查询商品数据。默认=总数+3个示例（答"多少款"）；
        full=true 返回在售全量行（全字段中文label）——答明细/整品类出单/导清单用。
        报数请用 total（别自己数行）；清单里查不到的型号即已下架或不存在。"""
        total = 0
        by_cat = {}
        samples = {}
        products = {}
        for key, t in TEMPLATES.items():
            n = app.state.conn.execute(
                f"SELECT COUNT(*) c FROM {t.table} WHERE status != 'delisted'").fetchone()['c']
            by_cat[t.name] = n
            total += n
            if full and (category is None or category == key):
                rows = app.state.conn.execute(
                    f"SELECT * FROM {t.table} WHERE status != 'delisted' ORDER BY id").fetchall()
                products[key] = [row_to_dict(t, r) for r in rows]
            else:
                row = app.state.conn.execute(
                    f"SELECT * FROM {t.table} WHERE status != 'delisted' LIMIT 3").fetchall()
                samples[key] = [row_to_dict(t, r) for r in row]
        out = {'total': total, 'by_category': by_cat,
               'category_keys': {t.name: k for k, t in TEMPLATES.items()},
               'samples': samples}
        if full:
            out['products'] = products
            out['note'] = ('products=各品类全部在售商品（全字段，id 可直接作 quote items 的 product_id）；'
                           '报数一律用 total；问某款详情从这里找，查不到=已下架或不存在；'
                           '整品类出报价单数量必须向用户确认')
        return out

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

    # ---- 商品直写（H5用户本人操作=即审批，不走工单）----
    @app.post('/products/{category}/direct')
    def direct_create(category: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            raise HTTPException(404, '未知品类')
        import secrets as _sec
        from . import inner_code as _ic
        t = TEMPLATES[category]
        pid = _sec.token_hex(8)
        cols = list(body.changes.keys())
        if not cols:
            raise HTTPException(400, 'changes 不能为空')
        conn_cols = [c for c in cols if c in dict(t.fields)]
        img_rels = []
        for fn in (body.images or []):
            src = os.path.join(app.state.storage.base, str(fn))
            if os.path.exists(src):
                ext = os.path.splitext(fn)[1] or '.png'
                img_rels.append(app.state.storage.save(category, pid, f'img{len(img_rels)}{ext}',
                                                     open(src, 'rb').read()))
        app.state.conn.execute(
            f"INSERT INTO {t.table}(id, inner_code, {', '.join(conn_cols)}, image_main, images) "
            f"VALUES({','.join('?' for _ in range(2 + len(conn_cols) + 2))})",
            (pid, _ic.gen(), *[str(body.changes[c]) for c in conn_cols],
             img_rels[0] if img_rels else '',
             json.dumps(img_rels, ensure_ascii=False) if img_rels else '[]'))
        app.state.conn.commit()
        from . import search
        if img_rels:
            search.reindex(app.state.conn, app.state.storage, category)
        return {'id': pid, 'inner_code': app.state.conn.execute(
            f'SELECT inner_code FROM {t.table} WHERE id=?', (pid,)).fetchone()['inner_code']}

    @app.patch('/products/{category}/{pid}/direct')
    def direct_update(category: str, pid: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        t = TEMPLATES[category]
        if body.changes:
            conn_cols = [c for c in body.changes if c in dict(t.fields)]
            sets = ', '.join(f'{c}=?' for c in conn_cols)
            if sets:
                app.state.conn.execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                             (*[str(body.changes[c]) for c in conn_cols], pid))
        if body.images:
            img_rels = []
            for fn in body.images:
                src = os.path.join(app.state.storage.base, str(fn))
                if os.path.exists(src):
                    ext = os.path.splitext(fn)[1] or '.png'
                    img_rels.append(app.state.storage.save(category, pid, f'img{len(img_rels)}{ext}',
                                                         open(src, 'rb').read()))
            if img_rels:
                app.state.conn.execute(f"UPDATE {t.table} SET image_main=?, images=?, updated_at=datetime('now') WHERE id=?",
                             (img_rels[0], json.dumps(img_rels, ensure_ascii=False), pid))
        app.state.conn.commit()
        from . import search
        search.reindex(app.state.conn, app.state.storage, category)
        return {'updated': True}

    @app.delete('/products/{category}/{pid}/direct')
    def direct_delete(category: str, pid: str, request: Request):
        _auth(request, app.state.token)
        t = TEMPLATES[category]
        app.state.conn.execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (pid,))
        app.state.conn.commit()
        return {'delisted': True}

    # ---- 报价单（v2：多商品+数量+百分比调整）----
    class QuoteItem(BaseModel):
        category: str
        product_id: str
        quantity: int = 1

    class QuoteIn(BaseModel):
        items: list[QuoteItem]
        price_adjustment_pct: float = 0
        deposit_pct: float = 30          # 定金百分比（30=30%），商家说"两成定金"传20

    @app.post('/quote')
    def do_quote(body: QuoteIn, request: Request):
        _auth(request, app.state.token)
        if not body.items:
            raise HTTPException(400, 'items 不能为空')
        import uuid
        from . import quote as quote_mod
        out_dir = os.path.join(app.state.storage.base, '_quotes')
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f'quote-{uuid.uuid4().hex[:8]}.xlsx')
        items = [{'category': i.category, 'product_id': i.product_id, 'quantity': i.quantity}
                 for i in body.items]
        quote_mod.generate_v2(app.state.conn, app.state.storage, items,
                              body.price_adjustment_pct, out,
                              deposit_pct=body.deposit_pct)
        from . import notify
        notify.push_file(f'📄 报价单已生成并发送（{len(items)} 款，调整 {body.price_adjustment_pct:+.0f}%）', out)
        return {'path': out}

    @app.get('/quote/{job_id}')
    def quote_status(job_id: str):
        from . import quote as quote_mod
        try:
            return quote_mod.job_status(job_id)
        except KeyError:
            raise HTTPException(404, 'no such job')

    # ---- C端：红线知识（微信 AI 对话 → 工具 → 审批 → 生效）----
    class RedlineIn(BaseModel):
        text_raw: str
        text_summary: str | None = None
        product_id: str | None = None

    @app.get('/cs/redline')
    def cs_redline_get(product_id: str | None = None, request: Request = None):
        _auth(request, app.state.token)
        from . import cs
        return cs.get_redline(app.state.conn, product_id or None)

    @app.post('/cs/redline')
    def cs_redline_set(body: RedlineIn, request: Request):
        """AI 工具入口：只建审批工单，批准才生效（与改价格同款管道）。"""
        _auth(request, app.state.token)
        text_raw = body.text_raw.strip()
        if not text_raw:
            raise HTTPException(400, 'text_raw 不能为空')
        from . import cs, tickets
        summary = body.text_summary or cs.summarize(text_raw)
        old = cs.get_redline(app.state.conn, body.product_id or None)
        payload = {'kind': 'redline', 'product_id': body.product_id or None,
                   'text_raw': text_raw, 'text_summary': summary,
                   'old_text_raw': old['text_raw']}          # 审批卡显示旧文→新文
        t = tickets.create(app.state.conn, 'redline', None, payload)
        _push_redline_card(t['id'], t['token'], body.product_id, old['text_raw'], text_raw)
        return {'ticket_id': t['id'], 'status': 'pending_approval'}

    # ---- C端：清单临时链接（客户网页查看/补改/导出）----
    @app.get('/cs/photo')
    def cs_photo(path: str, request: Request):
        _auth(request, app.state.token)
        base = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'cs_photos'))
        fp = os.path.abspath(path)
        if not fp.startswith(base + os.sep):          # 防路径穿越
            raise HTTPException(403, 'forbidden')
        if not os.path.exists(fp):
            raise HTTPException(404, 'no photo')
        return Response(content=open(fp, 'rb').read(), media_type='image/jpeg')

    def _link_conn(token):
        row = app.state.conn.execute('SELECT * FROM cs_link WHERE token=?', (token,)).fetchone()
        if row is None:
            raise HTTPException(404, '链接无效')
        return row

    @app.get('/cs/link/{token}')
    def cs_link_view(token: str):
        row = _link_conn(token)
        notes = app.state.conn.execute(
            "SELECT * FROM cs_note WHERE customer_id=? AND status='confirmed' "
            'ORDER BY id', (row['customer_id'],)).fetchall()
        return {'customer_id': row['customer_id'],
                'notes': [{'id': n['id'], 'photo': n['photo'],
                           'fields': json.loads(n['fields_json'])} for n in notes]}

    @app.patch('/cs/link/{token}/note/{note_id}')
    async def cs_link_edit(token: str, note_id: int, request: Request):
        _link_conn(token)
        body = await request.json()
        field, value = str(body.get('field', '')), str(body.get('value', ''))
        if not field:
            raise HTTPException(400, 'field 不能为空')
        row = app.state.conn.execute('SELECT * FROM cs_note WHERE id=?', (note_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '条目不存在')
        fields = json.loads(row['fields_json'])
        fields[field] = value
        app.state.conn.execute('UPDATE cs_note SET fields_json=? WHERE id=?',
                               (json.dumps(fields, ensure_ascii=False), note_id))
        app.state.conn.commit()
        return {'saved': True, 'fields': fields}

    @app.get('/cs/link/{token}/export.xlsx')
    def cs_link_export(token: str):
        import io

        import openpyxl
        row = _link_conn(token)
        notes = app.state.conn.execute(
            "SELECT * FROM cs_note WHERE customer_id=? AND status='confirmed' ORDER BY id",
            (row['customer_id'],)).fetchall()
        items = [json.loads(n['fields_json']) for n in notes]
        keys = []
        for f in items:                              # 动态字段列（保持出现顺序）
            for k in f:
                if k not in keys:
                    keys.append(k)
        wb = openpyxl.Workbook()
        ws = wb.active
        has_img_col = any('图' in k for k in keys)
        if has_img_col:
            from openpyxl.drawing.image import Image as XlImage
        ws.append(['序号', *keys])
        for i, f in enumerate(items, 1):
            ws.append([i, *[str(f.get(k, '')) for k in keys]])
        for r_i, n in enumerate(notes, start=2):      # 商品图嵌入（有图且装了 pillow）
            if not (n['photo'] and os.path.exists(n['photo'])):
                continue
            try:
                img = XlImage(n['photo'])
                img.width, img.height = 90, 90
                ws.add_image(img, f'A{r_i}')
                ws.row_dimensions[r_i].height = 70
            except Exception:                         # noqa: BLE001 pillow 缺失/图损坏 → 跳过
                pass
        for col, k in enumerate(keys, start=2):      # 列宽
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 16
        buf = io.BytesIO()
        wb.save(buf)
        from urllib.parse import quote
        fname = quote(f"清单-{row['customer_id'][:6]}.xlsx")
        return Response(content=buf.getvalue(),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})

