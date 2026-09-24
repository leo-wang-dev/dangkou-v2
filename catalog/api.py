import json
import os
import secrets
import time
from urllib.parse import quote as urlquote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, FiniteFloat

from . import ingest, tickets
from .templates import TEMPLATES, row_to_dict


_IMAGE_FORMAT_EXT = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp',
                     'BMP': '.bmp', 'TIFF': '.tif'}


def _validate_image_bytes(data: bytes) -> str:
    """Validate actual image content and return a canonical extension."""
    import io
    from PIL import Image, UnidentifiedImageError
    max_bytes = int(os.environ.get('CATALOG_UPLOAD_MAX_BYTES', 20 * 1024 * 1024))
    if not data:
        raise HTTPException(400, '图片文件为空')
    if len(data) > max_bytes:
        raise HTTPException(413, '图片超过 20MB，请压缩后重试')
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            detected = str(image.format or '').upper()
            if detected not in _IMAGE_FORMAT_EXT:
                raise HTTPException(400, '图片格式仅支持 PNG、JPEG、WEBP、BMP、TIFF')
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise HTTPException(400, '图片尺寸无效或像素过大')
            image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, '图片文件损坏或不是有效图片') from exc
    return _IMAGE_FORMAT_EXT[detected]


def _auth(request: Request, token: str):
    if not token:
        raise HTTPException(503, '服务未配置认证令牌，管理接口暂不可用')
    h = request.headers.get('X-Service-Token')
    if h != token and request.query_params.get('token') != token and request.query_params.get('auth') != token:
        raise HTTPException(401, 'unauthorized')


# wait 模式的模板解析等待上限：正常几秒完成；冷启动 LLM 慢时让位给出站推送。
TEMPLATE_WAIT_SEC = float(os.environ.get('CATALOG_TEMPLATE_WAIT_SEC', '90'))


class ImportIn(BaseModel):
    path: str
    category: str | None = None
    source_key: str | None = None
    mode: str | None = None
    category_key: str | None = None
    # The merchant-facing bot uses explicit two-stage imports.  None keeps the
    # old direct API callers backward-compatible while the plugin always sends
    # template/products explicitly.
    phase: str | None = None
    template_doc_id: int | None = None
    # 模板阶段等待模式：原地等解析出结果（上限 TEMPLATE_WAIT_SEC），把
    # 工单状态直接带回给调用方，出站推送只留给超时兜底。
    wait: bool = False


class DecisionIn(BaseModel):
    token: str
    approved: bool
    decisions: dict | None = None


class MutateIn(BaseModel):
    changes: dict
    product_id: str | None = None
    images: list[str] | None = None


def _push_redline_card(conn, ticket_id, token, product_id, old_text, new_text):
    """Queue the complete approval card; the durable notification worker retries failures."""
    base = (os.environ.get('CATALOG_V2_MANAGE_URL') or
            os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')).rstrip('/')
    auth = os.environ.get('CATALOG_V2_SERVICE_TOKEN', '')
    scope = f'商品 {product_id}' if product_id else '全店'
    link = f'{base}/cs/redline.html?i={ticket_id}&t={token}&auth={auth}'
    text = f'⚠️ 红线修改审批（{scope}）\n旧：{old_text}\n新：{new_text}\n点开确认：{link}'
    conn.execute("INSERT INTO cs_outbox(channel,body) VALUES('notify',?)", (text,))
    conn.commit()



def register_routes(app: FastAPI):
    from .merchant_binding import register as register_merchant
    register_merchant(app)
    import asyncio
    from contextvars import ContextVar
    from . import db
    current_connection = ContextVar('catalog_request_connection', default=None)
    memory_lock = asyncio.Lock()

    def request_conn():
        return current_connection.get() or app.state.conn

    @app.middleware('http')
    async def database_request(request, call_next):
        database = app.state.conn.execute('PRAGMA database_list').fetchone()[2]
        conn = db.connect(database) if database else app.state.conn
        context = current_connection.set(conn)

        async def respond():
            try:
                response = await call_next(request)
                if response.status_code >= 400:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Cache-Control'] = 'no-store'
            return response

        try:
            if database:
                return await respond()
            # In-memory fixtures have only one connection. Real deployments use
            # one connection per request so model/network waits cannot lock readers.
            async with memory_lock:
                return await respond()
        finally:
            current_connection.reset(context)
            if database:
                conn.close()

    @app.get('/health')
    def health():
        return {'status': 'ready', 'version': 'v2.0'}

    @app.get('/categories')
    def categories(request: Request):
        _auth(request, app.state.token)
        from . import dynamic_catalog
        conn = request_conn()
        templates = dynamic_catalog.list_templates(conn)
        # 空分类不上列表（预置剃须刀/卷发棒在新店没商品时不应出现）；
        # 显式按 key 访问 /products/{cat} 仍可用。
        counts = {}
        for t in TEMPLATES.values():
            counts[t.key] = conn.execute(
                f"SELECT COUNT(*) c FROM {t.table} WHERE status != 'delisted'").fetchone()['c']
        dyn = conn.execute("SELECT category_key, COUNT(*) c FROM product_dynamic "
                           "WHERE status != 'delisted' GROUP BY category_key").fetchall()
        counts.update({row['category_key']: row['c'] for row in dyn})
        # 空分类不上列表只针对预置剃须刀/卷发棒；动态分类（含手工新建的空分类）
        # 始终上列表——商家先建分类、再手工/自然语言加商品是正式流程。
        templates = [value for value in templates
                     if value.get('storage') == 'dynamic' or counts.get(value['key'], 0) > 0]
        return {'categories': templates}

    class CategoryCreateIn(BaseModel):
        name: str = Field(min_length=1, max_length=40)
        fields: list[dict]

    @app.post('/categories')
    def create_category(body: CategoryCreateIn, request: Request):
        """手工建分类（不经 Excel）：生成模板审批工单，批准后分类上页面。"""
        _auth(request, app.state.token)
        from . import dynamic_catalog, dynamic_import
        name = body.name.strip()
        if not name:
            raise HTTPException(400, '分类名不能为空')
        seen, fields_in = set(), []
        for item in body.fields or []:
            label = str((item or {}).get('label') or '').strip()
            if not label:
                continue
            if label in seen:
                raise HTTPException(400, f'字段名重复：{label}')
            seen.add(label)
            visibility = item.get('visibility')
            fields_in.append({'label': label[:40],
                              'visibility': visibility if visibility in ('public', 'internal') else 'public'})
        if not fields_in:
            raise HTTPException(400, '至少提供一个字段')
        if len(fields_in) > 40:
            raise HTTPException(400, '字段数超过 40，请精简')
        payload = dynamic_import.manual_template_payload(name, fields_in)
        key = payload['sheets'][0]['template']['key']
        try:
            dynamic_catalog.get_template(request_conn(), key)
            exists = True
        except KeyError:
            exists = False
        # 同名在途工单也要挡：两张 create 工单先后批准会在第二张上报“已过期”。
        pending = request_conn().execute(
            "SELECT 1 FROM approval_ticket WHERE status='pending' AND ticket_type='template_import' "
            "AND json_extract(payload,'$.sheets[0].template.key')=?", (key,)).fetchone()
        if exists or pending:
            raise HTTPException(409, f'同名分类已存在或在审批中：{name}（可改名或让商家换名字）')
        tk = tickets.create(request_conn(), 'template_import', None, payload)
        return {'ticket_id': tk['id'], 'token': tk['token'],
                'name': name, 'fields': len(fields_in)}

    class CategoryRenameIn(BaseModel):
        name: str | None = Field(default=None, min_length=1, max_length=64)
        supplier: str | None = Field(default=None, max_length=40)

    @app.patch('/categories/{category}')
    def rename_category(category: str, body: CategoryRenameIn, request: Request):
        """动态分类改名/改供应商：Sheet 名是默认名（如 Sheet1）时商家的自救口。"""
        _auth(request, app.state.token)
        from . import dynamic_catalog
        conn = request_conn()
        if body.supplier is not None:
            row = conn.execute('SELECT 1 FROM category_template WHERE key=? AND storage=\'dynamic\'',
                               (category,)).fetchone()
            if row is None:
                raise HTTPException(404, '未知动态分类')
            conn.execute('UPDATE category_template SET supplier=? WHERE key=?',
                         (body.supplier.strip()[:40], category))
            conn.commit()
        if body.name:
            try:
                dynamic_catalog.rename_template(conn, category, body.name)
            except KeyError:
                raise HTTPException(404, '未知分类')
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        template = dynamic_catalog.get_template(conn, category)
        return {'key': template['key'], 'name': template['name'],
                'supplier': template.get('supplier') or ''}

    @app.get('/stats/supplier')
    def stats_supplier(request: Request, by: str = 'supplier'):
        """按供应商/分类聚合在售统计（动态分类）：供应商搜索与“多少款在推广”的底数。

        与既有 /stats（预置分类库存口径）互不影响；推广口径=对客户可见。
        """
        _auth(request, app.state.token)
        conn = request_conn()
        rows = conn.execute(
            "SELECT t.key cat_key, t.name cat_name, COALESCE(NULLIF(t.supplier,''), t.name) supplier,"
            " COALESCE(SUM(p.status!='delisted'),0) total,"
            " COALESCE(SUM(p.status!='delisted' AND p.cs_visible=1),0) visible"
            " FROM category_template t LEFT JOIN product_dynamic p ON p.category_key=t.key"
            " WHERE t.storage='dynamic' GROUP BY t.key").fetchall()
        items = [dict(r) for r in rows]
        if by == 'category':
            return {'by': 'category', 'categories': items}
        groups: dict = {}
        for item in items:
            g = groups.setdefault(item['supplier'], {'supplier': item['supplier'], 'total': 0, 'visible': 0, 'categories': []})
            g['total'] += item['total']; g['visible'] += item['visible']
            g['categories'].append({'key': item['cat_key'], 'name': item['cat_name'],
                                    'total': item['total'], 'visible': item['visible']})
        return {'by': 'supplier', 'suppliers': sorted(groups.values(), key=lambda g: -g['total'])}

    class QuoteMapIn(BaseModel):
        price_field: str = Field(min_length=1, max_length=128)
        model_field: str = Field(min_length=1, max_length=128)
        ctn_field: str | None = Field(default=None, max_length=128)
        color_field: str | None = Field(default=None, max_length=128)

    @app.get('/categories/{category}/quote-map')
    def get_quote_map(category: str, request: Request):
        _auth(request, app.state.token)
        from . import dynamic_catalog
        try:
            template = dynamic_catalog.get_template(request_conn(), category)
        except KeyError:
            raise HTTPException(404, '未知分类')
        if template['storage'] != 'dynamic':
            raise HTTPException(400, '预置分类的报价映射是固定配置')
        return {'key': category, 'name': template['name'],
                'quote_map': template.get('quote_map') or {},
                'quotable': dynamic_catalog.quotable(template),
                'suggestion': dynamic_catalog.suggest_quote_map(template['fields']),
                'fields': [{'key': f['key'], 'label': f['label']} for f in template['fields']]}

    @app.put('/categories/{category}/quote-map')
    def put_quote_map(category: str, body: QuoteMapIn, request: Request):
        """指定该分类哪一列是报价/型号/箱规/颜色；字段名（中文表头）或字段 key 均可。"""
        _auth(request, app.state.token)
        from . import dynamic_catalog
        try:
            merged = dynamic_catalog.set_quote_map(request_conn(), category, body.model_dump())
        except KeyError:
            raise HTTPException(404, '未知分类')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {'key': category, 'quote_map': merged, 'quotable': True}

    class CategoryVisibilityIn(BaseModel):
        visible: bool

    @app.patch('/categories/{category}/visibility')
    def set_category_visibility(category: str, body: CategoryVisibilityIn, request: Request):
        """整分类客户可见性：一次设置该分类全部在售商品的可观测（cs_visible）。"""
        _auth(request, app.state.token)
        conn = request_conn()
        if category in TEMPLATES:
            table = TEMPLATES[category].table
        else:
            from . import dynamic_catalog
            try:
                template = dynamic_catalog.get_template(conn, category)
            except KeyError:
                raise HTTPException(404, '未知分类')
            if template['storage'] != 'dynamic':
                raise HTTPException(400, '预置分类的可见性请用 razor/curler key 设置')
            table = 'product_dynamic'
        if table == 'product_dynamic':
            cur = conn.execute("UPDATE product_dynamic SET cs_visible=?, updated_at=datetime('now') "
                               "WHERE category_key=? AND status!='delisted'",
                               (1 if body.visible else 0, category))
        else:
            cur = conn.execute(f"UPDATE {table} SET cs_visible=?, updated_at=datetime('now') "
                               "WHERE status!='delisted'", (1 if body.visible else 0,))
        conn.commit()
        return {'key': category, 'visible': body.visible, 'updated': cur.rowcount}

    @app.get('/categories/{category}/template.xlsx')
    def category_template_xlsx(category: str, request: Request):
        _auth(request, app.state.token)
        from . import dynamic_catalog
        try:
            template = dynamic_catalog.get_template(request_conn(), category)
        except KeyError:
            raise HTTPException(404, '未知分类')
        import io
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        workbook = Workbook()
        sheet = workbook.active
        safe_title = ''.join('_' if char in '\\/*?:[]' else char for char in template['name'])[:31] or '商品'
        sheet.title = safe_title
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(template['fields'])))
        sheet.cell(1, 1, template['name'] + '商品导入模板')
        sheet.cell(1, 1).font = Font(bold=True, size=16)
        for column, field in enumerate(template['fields'], 1):
            cell = sheet.cell(2, column, field['label'])
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='183B56')
            cell.alignment = Alignment(wrap_text=True, vertical='center')
            example = {'sequence': 1, 'model': '示例型号', 'image': '在本单元格插入商品图片',
                       'stock': '100', 'cost': '35.00', 'price': '39.00',
                       'note': '内部备注', 'spec': '示例规格'}.get(field['role'], '示例内容')
            sheet.cell(3, column, example)
            sheet.column_dimensions[sheet.cell(2, column).column_letter].width = max(14, min(32, len(field['label']) * 2 + 4))
        sheet.freeze_panes = 'A3'
        output = io.BytesIO(); workbook.save(output)
        filename = urlquote(template['name'] + '商品导入模板.xlsx')
        return Response(content=output.getvalue(),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})

    @app.get('/cs/catalog')
    def customer_catalog(request: Request):
        _auth(request, os.environ.get('CATALOG_CS_SERVICE_TOKEN') or app.state.token)
        from .customer_catalog import local_catalog
        return local_catalog(request_conn())

    @app.get('/cs/catalog/{category}/{product_id}/photo')
    def customer_catalog_photo(category: str, product_id: str, request: Request):
        _auth(request, os.environ.get('CATALOG_CS_SERVICE_TOKEN') or app.state.token)
        from . import customer_catalog, shop_link
        product = next((value for value in customer_catalog.local_catalog(request_conn())['products']
                        if value['_category'] == category and value['id'] == product_id), None)
        if not product or not product.get('image_main'):
            raise HTTPException(404, '商品图片不可用')
        try:
            data = app.state.storage.read(product['image_main'])
        except (OSError, ValueError):
            raise HTTPException(404, '商品图片不可用')
        import mimetypes
        media = mimetypes.guess_type(product['image_main'])[0]
        if media not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
            media = 'application/octet-stream'
        return Response(content=data, media_type=media, headers={
            'X-Shop-Id': shop_link.profile(request_conn())['shop_id'],
            'X-Filename': os.path.basename(product['image_main']),
            'X-Content-Type-Options': 'nosniff'})

    class CustomerSearchIn(BaseModel):
        fields: list[dict[str, str]] = Field(max_length=50)
        image_base64: str = Field(max_length=16 * 1024 * 1024)

    @app.post('/cs/catalog/search')
    def customer_catalog_search(body: CustomerSearchIn, request: Request):
        _auth(request, os.environ.get('CATALOG_CS_SERVICE_TOKEN') or app.state.token)
        import base64
        import binascii
        from . import photo_inquiry, shop_link
        try:
            photo = base64.b64decode(body.image_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, '图片编码无效')
        return {'shop_id': shop_link.profile(request_conn())['shop_id'],
                'candidates': photo_inquiry.local_candidates(request_conn(), body.fields, photo)}

    @app.get('/ready')
    def readiness(request: Request):
        _auth(request, app.state.token)
        from scripts.preflight import missing
        from . import cs
        errors = missing()
        try:
            request_conn().execute('SELECT 1').fetchone()
            profile = cs.get_shop(request_conn())
            if not all(profile.get(k) for k in ('owner_tg_username','owner_wechat')):
                errors.append('老板 TG 和微信联系方式未补齐')
            if not profile.get('shop_name') or not profile.get('tg_bot_id'):
                errors.append('档口名称和 TG bot 身份尚未绑定')
            pending = request_conn().execute('SELECT COUNT(*) FROM cs_outbox WHERE sent=0 AND attempts>0').fetchone()[0]
            if pending:
                errors.append('存在发送失败待重试的消息')
        except Exception:
            errors.append('数据库不可用')
        return Response(json.dumps({'ready': not errors, 'blockers': errors}, ensure_ascii=False),
                        status_code=503 if errors else 200, media_type='application/json')

    @app.get('/img/{rel:path}')
    def img(rel: str, request: Request):
        _auth(request, app.state.token)
        try:
            data = app.state.storage.read(rel)
        except FileNotFoundError:
            raise HTTPException(404, 'no image')
        extension = os.path.splitext(rel)[1].lower()
        media_type = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.webp': 'image/webp', '.bmp': 'image/bmp',
            '.tif': 'image/tiff', '.tiff': 'image/tiff',
        }.get(extension, 'application/octet-stream')
        return Response(content=data, media_type=media_type)

    @app.post('/upload')
    async def upload(request: Request):
        """图片上传 → 暂存 data/images/_upload/（审批换图/商品换图用）。"""
        _auth(request, app.state.token)
        import uuid
        from fastapi import UploadFile, File
        form = await request.form()
        up = form.get('file')
        if up is None or not hasattr(up, 'read'):
            raise HTTPException(400, '请上传 file 文件')
        max_bytes = int(os.environ.get('CATALOG_UPLOAD_MAX_BYTES', 20 * 1024 * 1024))
        data = await up.read(max_bytes + 1)
        ext = _validate_image_bytes(data)
        rel = app.state.storage.save('_upload', uuid.uuid4().hex[:12], 'image' + ext, data)
        return {'path': rel}

    # ---- 导入 ----
    @app.post('/import')
    def do_import(body: ImportIn, request: Request):
        _auth(request, app.state.token)
        if not os.path.isfile(body.path):
            raise HTTPException(404, f'文件不存在: {body.path}')
        if body.mode not in (None, 'new', 'existing'):
            raise HTTPException(400, '导入 mode 只能是 new 或 existing')
        if body.mode == 'existing' and not body.category_key:
            raise HTTPException(400, '并入已有分类时必须提供 category_key')
        if body.mode != 'existing' and body.category_key:
            raise HTTPException(400, '只有 existing 模式可以提供 category_key')
        if body.phase not in (None, 'template', 'products'):
            raise HTTPException(400, '导入 phase 只能是 template 或 products')
        if body.phase == 'products' and (body.template_doc_id is None or body.template_doc_id <= 0):
            raise HTTPException(400, '商品导入必须提供已审批的 template_doc_id')
        if body.phase != 'products' and body.template_doc_id is not None:
            raise HTTPException(400, '只有 products 阶段可以提供 template_doc_id')
        # 实测 91MB/378 图解析<1s；按每 MB 4s 估并封顶 10 分钟，宁可报短不报长。
        est = min(600, max(60, int(os.path.getsize(body.path) / 1048576 * 4)))
        # Dynamic Sheet imports default to the safe template-only phase even
        # for older callers that do not know the new field.  Fixed legacy
        # callers keep their old path unless the bot explicitly sends a phase.
        phase = body.phase or ('template' if body.category is None else 'legacy')
        # 模板/商品解析实测都只要几秒：wait 模式原地等结果，让调用方（bot）
        # 一条消息把结果和审批入口说清。否则后台完成推送可能抢在对话回复
        # 前面落地，商家会先看到“已完成”再看到“开始解析”的倒序消息。
        want_wait = body.wait and phase in ('template', 'products')
        real_callback = app.state.callback
        inline_mode = {'on': want_wait}

        def wait_callback(**kw):
            # inline 模式下完成通知由本请求原样带回；超时后 route 会先关掉
            # inline_mode 再返回，之后的完成才走出站推送。
            if inline_mode['on']:
                return
            if real_callback:
                real_callback(**kw)

        try:
            doc_id = ingest.start(request_conn(), app.state.storage,
                                  body.path, body.category,
                                  callback=wait_callback, source_key=body.source_key,
                                  mode=body.mode, category_key=body.category_key,
                                  phase=phase,
                                  template_doc_id=body.template_doc_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        def inline(s):
            out = {'doc_id': doc_id, 'status': s['status'], 'phase': s['phase'],
                   'stats': s['stats']}
            if s['status'] == 'failed':
                out['error'] = s['error']
            return out

        if want_wait:
            deadline = time.monotonic() + TEMPLATE_WAIT_SEC
            while time.monotonic() < deadline:
                s = ingest.status(request_conn(), doc_id)
                if s['status'] in ('ticketed', 'failed'):
                    return inline(s)
                time.sleep(0.4)
            # 超时兜底：先放出站推送，再补查一次状态——覆盖“回调刚被吞、
            # 状态其实已落库”的窗口，保证完成通知不丢。
            inline_mode['on'] = False
            s = ingest.status(request_conn(), doc_id)
            if s['status'] in ('ticketed', 'failed'):
                return inline(s)
        return {'doc_id': doc_id,
                'est_sec': est}

    @app.get('/import/{doc_id}')
    def import_status(doc_id: int, request: Request):
        _auth(request, app.state.token)
        try:
            return ingest.status(request_conn(), doc_id)
        except KeyError:
            raise HTTPException(404, 'no such doc')

    # ---- 工单 ----
    @app.get('/tickets')
    def list_tickets(request: Request):
        _auth(request, app.state.token)
        rows = request_conn().execute(
            'SELECT id, ticket_type, category, status, token, created_at '
            'FROM approval_ticket ORDER BY id DESC').fetchall()
        # token 只对 pending 暴露（审批链接要带它；已决工单不回）
        return {'tickets': [
            {**dict(r), 'token': (r['token'] if r['status'] == 'pending' else None)}
            for r in rows]}

    @app.get('/tickets/{ticket_id}')
    def ticket_detail(ticket_id: int, request: Request):
        import json as _json
        r = request_conn().execute(
            'SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
        if r is None:
            raise HTTPException(404, 'no such ticket')
        _ticket_auth(request, r)
        payload = _json.loads(r['payload'])
        # 行级决策钥匙：new 行=_rid，update/delist 行=商品 id；顺带补图片预览地址
        wd = payload.get('work_dir')
        preview_rows = list(payload.get('drafts', {}).get('new', []))
        preview_rows += [p[1] if isinstance(p, list) else p for p in payload.get('drafts', {}).get('update', [])]
        for sheet in payload.get('sheets', []):
            preview_rows += list(sheet.get('drafts', {}).get('new', []))
            preview_rows += [p[1] if isinstance(p, list) else p
                             for p in sheet.get('drafts', {}).get('update', [])]
        for d in preview_rows:
            d.setdefault('_rid', None)
            fns = [f for f in (d.get('images') or []) if f] or \
                  ([d['image_main']] if d.get('image_main') else [])
            if fns:
                d['_imgs'] = [
                    f"/ticketimg/{ticket_id}/{urlquote(fn)}?t={r['token']}"
                    for fn in fns]
                d['_img'] = d['_imgs'][0]
        return {'ticket': {'id': r['id'], 'ticket_type': r['ticket_type'],
                           'category': r['category'], 'status': r['status'],
                           'created_at': r['created_at']},
                'payload': payload}

    def _ticket_auth(request, row):
        supplied = request.query_params.get('t') or request.headers.get('X-Ticket-Token', '')
        if supplied and secrets.compare_digest(supplied, row['token']):
            return
        _auth(request, app.state.token)

    @app.get('/ticketimg/{ticket_id}/{fname:path}')
    def ticket_img(ticket_id: int, fname: str, request: Request):
        import json as _json
        import os as _os
        r = request_conn().execute('SELECT * FROM approval_ticket WHERE id=?', (ticket_id,)).fetchone()
        if r is None:
            raise HTTPException(404, 'no such ticket')
        _ticket_auth(request, r)
        payload = _json.loads(r['payload'])
        allowed = set()
        preview_rows = list(payload.get('drafts', {}).get('new', []))
        preview_rows += [p[1] if isinstance(p, list) else p for p in payload.get('drafts', {}).get('update', [])]
        for sheet in payload.get('sheets', []):
            preview_rows += list(sheet.get('drafts', {}).get('new', []))
            preview_rows += [p[1] if isinstance(p, list) else p
                             for p in sheet.get('drafts', {}).get('update', [])]
        for d in preview_rows:
            allowed.update(d.get('images') or [])
            if d.get('image_main'):
                allowed.add(d['image_main'])
        if fname not in allowed:
            raise HTTPException(404, 'no image')
        safe = _os.path.basename(fname)
        if fname.startswith('_upload/'):
            p = app.state.storage.abs_path(fname)
        else:
            wd = payload.get('work_dir')
            if not wd:
                raise HTTPException(404, 'no preview')
            if fname.split('/')[0] in TEMPLATES:
                p = app.state.storage.abs_path(fname)
                wd = app.state.storage.base
            else:
                p = _os.path.realpath(_os.path.join(wd, fname))
            if _os.path.commonpath([_os.path.realpath(wd), p]) != _os.path.realpath(wd):
                raise HTTPException(403, 'forbidden')
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
            result = tickets.decide_row(request_conn(), ticket_id,
                                        body.token, body.row_key,
                                        body.approved, body.edits, before_commit=_persist_decision_images)
        except tickets.TicketError as e:
            raise HTTPException(409 if isinstance(e, tickets.TicketConflict) else 400, str(e))
        if body.approved:
            _reindex_decision(result)
            if result.get('phase') == 'template' and result.get('template_doc_id'):
                # The first callback announces the template ticket.  This
                # second durable outbox message is sent only after approval,
                # so the merchant is reminded at the exact hand-off point.
                try:
                    from . import notify
                    notify.push(result['template_doc_id'], ticket_id, body.token,
                                {'phase': 'template', 'approved': True,
                                 'template_count': result.get('template_approved', 0)},
                                conn=request_conn())
                except Exception:
                    pass
        return result

    class DraftEditIn(BaseModel):
        token: str
        row_key: str
        edits: dict

    @app.patch('/tickets/{ticket_id}/draft')
    def save_draft(ticket_id: int, body: DraftEditIn):
        try:
            return tickets.save_draft_edit(request_conn(), ticket_id,
                                           body.token, body.row_key, body.edits)
        except tickets.TicketError as e:
            raise HTTPException(400, str(e))

    @app.post('/tickets/{ticket_id}/decision')
    def decide_ticket(ticket_id: int, body: DecisionIn):
        try:
            result = tickets.decide(request_conn(), ticket_id, body.token,
                                    body.approved, body.decisions, before_commit=_persist_decision_images)
        except tickets.TicketError as e:
            raise HTTPException(409 if isinstance(e, tickets.TicketConflict) else 400, str(e))
        if body.approved:
            _reindex_decision(result)
            if result.get('phase') == 'template' and result.get('template_doc_id'):
                # Whole-ticket approval is the normal H5 path.  Queue the
                # hand-off reminder only after the template commit succeeds.
                try:
                    from . import notify
                    notify.push(result['template_doc_id'], ticket_id, body.token,
                                {'phase': 'template', 'approved': True,
                                 'template_count': result.get('template_approved', 0)},
                                conn=request_conn())
                except Exception:
                    pass
        return result

    def _persist_decision_images(result):
        if result.get('created_rows'):
            _persist_images_and_reindex(result)
        if result.get('images_applied'):
            _apply_product_images(result['images_applied'])

    def _reindex_decision(result):
        from . import search
        cats = {r['_category'] for r in result.get('created_rows', [])}
        if result.get('images_applied'):
            cats.update(k for k, t in TEMPLATES.items() if t.table == result['images_applied']['table'])
        if not cats:
            return
        db_file = app.state.conn.execute('PRAGMA database_list').fetchone()[2]
        if not db_file:
            # 内存库（测试环境）：保持同步，断言可即时看到向量
            for cat in cats:
                search.reindex(request_conn(), app.state.storage, cat)
            return
        # 决策接口不等待嵌入：商品图向量后台补齐（reindex 幂等、失败自动退避重试），
        # 大批量图片的导入审批不再把请求拖到浏览器/网关超时。
        import threading
        def _bg():
            from . import db as _db
            conn = _db.connect(db_file)
            try:
                for cat in cats:
                    search.reindex(conn, app.state.storage, cat)
            except Exception:  # noqa: BLE001
                pass
            finally:
                conn.close()
        threading.Thread(target=_bg, daemon=True).start()

    def _read_image_source(fn, work_dir=None):
        try:
            if work_dir is not None and str(fn).split('/')[0] not in {'_upload', *TEMPLATES}:
                base = os.path.realpath(work_dir)
                src = os.path.realpath(os.path.join(base, str(fn)))
                if os.path.commonpath([base, src]) != base:
                    raise FileNotFoundError('outside work directory')
            else:
                src = app.state.storage.abs_path(str(fn))
            return open(src, 'rb').read()
        except (OSError, ValueError) as exc:
            raise HTTPException(400, '图片无法读取，请重新上传') from exc

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
            data, ext = _webpify(_read_image_source(fn), fn)
            rels.append(app.state.storage.save(cat, pid, f'img-{secrets.token_hex(8)}{ext}', data))
        if not rels and info['images']:
            raise HTTPException(400, '图片无法读取')
        request_conn().execute(
            f"UPDATE {table} SET image_main=?, images=?, updated_at=datetime('now') WHERE id=?",
            (rels[0] if rels else '', __import__('json').dumps(rels, ensure_ascii=False), pid))
        request_conn().execute('DELETE FROM embedding WHERE product_id=?', (pid,))

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
            if not fn_list:
                continue
            rels = []
            for i, fn in enumerate(fn_list):
                fn = str(fn)
                data, ext = _webpify(_read_image_source(fn, wd), fn)
                rels.append(app.state.storage.save(row['_category'], row['id'],
                                                   f'img-{secrets.token_hex(8)}{ext}', data))
            if rels:
                request_conn().execute('DELETE FROM embedding WHERE product_id=?', (row['id'],))
                image_column = 'images_json' if row['_table'] == 'product_dynamic' else 'images'
                request_conn().execute(
                    f"UPDATE {row['_table']} SET image_main=?, {image_column}=? WHERE id=?",
                    (rels[0], __import__('json').dumps(rels, ensure_ascii=False), row['id']))
            cats.add(row['_category'])

    # ---- 商品（读直查；写=审批工单）----
    @app.get('/products/{category}')
    def list_products(category: str, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            from . import dynamic_catalog
            try:
                template = dynamic_catalog.get_template(request_conn(), category)
            except KeyError:
                raise HTTPException(404, '未知品类')
            rows = dynamic_catalog.list_products(request_conn(), category)
            products = []
            for row in rows:
                product = {field['label']: row['data'].get(field['key'], '')
                           for field in template['fields']}
                product.update({'内部货号': row['inner_code'], '状态': row['status'],
                                '主图': row['image_main'], '图集': row['images'],
                                '可观测': row['cs_visible'], 'id': row['id']})
                products.append(product)
            fields = [{**field, 'col': field['key']} for field in template['fields']]
            fields.append({'col': 'cs_visible', 'label': '可观测', 'type': 'number',
                           'role': 'visibility', 'visibility': 'internal',
                           'required': False, 'searchable': False})
            return {'template': {**template, 'fields': fields}, 'products': products}
        t = TEMPLATES[category]
        rows = request_conn().execute(
            f'SELECT * FROM {t.table} ORDER BY id').fetchall()
        return {'template': {'key': t.key, 'name': t.name,
                             'fields': [{'col': c, 'label': l} for c, l in (*t.fields, ('cs_visible', '可观测'))]},
                'products': [row_to_dict(t, r) for r in rows]}

    def _check_cs_visible(category, pid, changes):
        from . import cs
        if category not in TEMPLATES:
            raise HTTPException(404, '未知品类')
        try:
            cs.validate_product(request_conn(), TEMPLATES[category], pid, changes)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post('/products/{category}')
    def create_product(category: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            return _dynamic_mutation_ticket(category, 'create', body)
        try:
            norm, to_remark = tickets.normalize_changes(TEMPLATES[category], body.changes or {})
        except tickets.TicketError as exc:
            raise HTTPException(400,str(exc))
        if not set(body.changes or {}) - set(to_remark) and body.images is None:
            _raise_unknown_fields(category)   # 一个合法字段都没有=AI没做映射，打回让它重发
        _check_cs_visible(category, None, norm)
        tk = tickets.create(request_conn(), 'mutate', category,
                            {'kind': 'mutate', 'action': 'create',
                             'product_id': None, 'changes': norm,
                             **({'images': body.images} if body.images is not None else {})})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.patch('/products/{category}/{pid}')
    def update_product(category: str, pid: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if not body.changes and body.images is None:
            raise HTTPException(400, 'changes 与 images 至少给一个')
        if category not in TEMPLATES:
            return _dynamic_mutation_ticket(category, 'update', body, pid)
        try:
            norm, to_remark = tickets.normalize_changes(TEMPLATES[category], body.changes or {})
        except tickets.TicketError as exc:
            raise HTTPException(400,str(exc))
        if not set(body.changes or {}) - set(to_remark) and body.images is None:
            _raise_unknown_fields(category)
        _check_cs_visible(category, pid, norm)
        tk = tickets.create(request_conn(), 'mutate', category,
                            {'kind': 'mutate', 'action': 'update', 'product_id': pid,
                             'changes': norm,
                             **({'images': body.images} if body.images is not None else {}),
                             'before': _current(category, pid)})
        return {'ticket_id': tk['id'], 'token': tk['token']}

    @app.delete('/products/{category}/{pid}')
    def delete_product(category: str, pid: str, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            return _dynamic_mutation_ticket(category, 'delete', MutateIn(changes={}), pid)
        tk = tickets.create(request_conn(), 'mutate', category,
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
        if category not in TEMPLATES:
            from . import dynamic_catalog
            try:
                template = dynamic_catalog.get_template(request_conn(), category)
                row = next(value for value in dynamic_catalog.list_products(request_conn(), category)
                           if value['id'] == pid)
            except (KeyError, StopIteration):
                return None
            value = {field['label']: row['data'].get(field['key'], '') for field in template['fields']}
            value.update({'id': row['id'], '内部货号': row['inner_code'], '状态': row['status'],
                          '主图': row['image_main'], '图集': row['images'], '可观测': row['cs_visible']})
            return value
        t = TEMPLATES[category]
        r = request_conn().execute(
            f'SELECT * FROM {t.table} WHERE id=?', (pid,)).fetchone()
        return row_to_dict(t, r) if r else None

    def _dynamic_mutation_ticket(category, action, body, pid=None):
        from . import dynamic_catalog
        try:
            template = dynamic_catalog.get_template(request_conn(), category)
        except KeyError:
            raise HTTPException(404, '未知品类')
        if template['storage'] != 'dynamic':
            raise HTTPException(400, '预置品类请使用原商品接口')
        mapping = {field['key']: field['key'] for field in template['fields']}
        mapping.update({field['label']: field['key'] for field in template['fields']})
        normalized = {}
        unknown = []
        for key, value in (body.changes or {}).items():
            if key == 'cs_visible' or key in ('可观测', '对客户可见'):
                normalized['cs_visible'] = str(value).strip()
            elif key in mapping:
                normalized[mapping[key]] = str(value).strip()
            else:
                unknown.append(key)
        if unknown:
            raise HTTPException(400, '无法识别字段：' + '、'.join(unknown) + '。请先查询该分类模板。')
        if action == 'create' and not normalized and body.images is None:
            raise HTTPException(400, '新增商品至少需要一个字段或一张图片')
        if normalized.get('cs_visible') not in (None, '0', '1'):
            raise HTTPException(400, '可观测只能是 0 或 1')
        row = None
        if action != 'create':
            row = next((value for value in dynamic_catalog.list_products(request_conn(), category)
                        if value['id'] == pid), None)
            if row is None:
                raise HTTPException(404, '商品不存在')
        payload = {'kind': 'dynamic_mutate', 'action': action, 'product_id': pid,
                   'template_version': template['version'], 'changes': normalized,
                   'before': _current(category, pid) if row else None,
                   'before_snapshot': tickets._dynamic_product_snapshot(row)}
        if body.images is not None:
            payload['images'] = body.images
        ticket = tickets.create(request_conn(), 'mutate', category, payload)
        return {'ticket_id': ticket['id'], 'token': ticket['token']}

    # ---- 统计查询 ----
    @app.get('/stats')
    def stats(request: Request, full: bool = False, category: str | None = None):
        """查询商品数据。默认=总数+3个示例（答"多少款"）；
        full=true 返回在售全量行（全字段中文label）——答明细/整品类出单/导清单用。
        报数请用 total（别自己数行）；清单里查不到的型号即已下架或不存在。"""
        _auth(request, app.state.token)
        total = 0
        visible_total = 0
        by_cat = {}
        visible_by_cat = {}
        samples = {}
        products = {}
        from . import dynamic_catalog
        dynamic_templates = [value for value in dynamic_catalog.list_templates(request_conn())
                             if value['storage'] == 'dynamic']
        known_categories = {*TEMPLATES, *(value['key'] for value in dynamic_templates)}
        if category is not None and category not in known_categories:
            raise HTTPException(404, '未知品类')
        category_keys = {t.name: key for key, t in TEMPLATES.items()}
        for key, t in TEMPLATES.items():
            if category is not None and key != category:
                continue
            n = request_conn().execute(
                f"SELECT COUNT(*) c FROM {t.table} WHERE status != 'delisted'").fetchone()['c']
            visible = request_conn().execute(
                f"SELECT COUNT(*) c FROM {t.table} WHERE status != 'delisted' AND cs_visible=1").fetchone()['c']
            # 空分类不进清单（新店的预置剃须刀/卷发棒不该凭空出现）；
            # 商家显式点名查询时如实返回 0 款。
            if n == 0 and category is None:
                continue
            by_cat[t.name] = n
            visible_by_cat[t.name] = visible
            total += n
            visible_total += visible
            if full and (category is None or category == key):
                rows = request_conn().execute(
                    f"SELECT * FROM {t.table} WHERE status != 'delisted' ORDER BY id").fetchall()
                products[key] = [row_to_dict(t, r) for r in rows]
            else:
                row = request_conn().execute(
                    f"SELECT * FROM {t.table} WHERE status != 'delisted' LIMIT 3").fetchall()
                samples[key] = [row_to_dict(t, r) for r in row]
        for template in dynamic_templates:
            key = template['key']
            if category is not None and category != key:
                continue
            rows = [row for row in dynamic_catalog.list_products(request_conn(), key)
                    if row['status'] != 'delisted']
            if not rows and category is None:
                continue
            category_keys[template['name']] = key
            by_cat[template['name']] = len(rows)
            visible_by_cat[template['name']] = sum(1 for row in rows if row['cs_visible'])
            total += len(rows)
            visible_total += visible_by_cat[template['name']]
            values = []
            for row in rows if full else rows[:3]:
                value = {field['label']: row['data'].get(field['key'], '')
                         for field in template['fields']}
                value.update({'id': row['id'], '内部货号': row['inner_code'],
                              '状态': row['status'], '主图': row['image_main'],
                              '图集': row['images'], '可观测': row['cs_visible']})
                values.append(value)
            (products if full else samples)[key] = values
        from . import shop_link
        identity = shop_link.profile(request_conn())
        categories = [{'key': category_keys[name], 'name': name,
                       'total': count, 'customer_visible': visible_by_cat.get(name, 0)}
                      for name, count in by_cat.items()]
        out = {'shop_id': identity['shop_id'], 'shop_name': identity['shop_name'],
               'total': total, 'customer_visible_total': visible_total,
               'by_category': by_cat, 'visible_by_category': visible_by_cat,
               'categories': categories, 'category_keys': category_keys,
               'samples': samples}
        if full:
            out['products'] = products
            out['note'] = ('products=各品类全部在售商品（全字段，id 可直接作 quote items 的 product_id）；'
                           '报数一律用 total；问某款详情从这里找，查不到=已下架或不存在；'
                           '整品类出报价单数量必须向用户确认')
        return out

    # ---- 检索 ----
    class SearchIn(BaseModel):
        image_path: str = Field(min_length=1, max_length=4096)
        top_k: int = Field(default=5, ge=1, le=20)
        exclude_ids: list[str] = Field(default_factory=list, max_length=100)

    @app.post('/search')
    def do_search(body: SearchIn, request: Request):
        _auth(request, app.state.token)
        from . import search
        if not os.path.isfile(body.image_path):
            raise HTTPException(404, '图片不存在')
        data = open(body.image_path, 'rb').read(
            int(os.environ.get('CATALOG_UPLOAD_MAX_BYTES', 20 * 1024 * 1024)) + 1)
        extension = _validate_image_bytes(data)
        # 微信连接器保存的文件经常是 .bin；用已验证出的真实格式给视觉模型，
        # 避免把本地临时文件名误当成图片 MIME 类型。
        # 嵌入失败必须转成 503+可读文案：裸 500 会把引擎侧模型逼成自由发挥，
        # 且 401（凭据失效）与超时对用户的含义不同，分开说清。
        import requests as _requests
        try:
            vec = search.embed_image(data, 'query' + extension)
        except _requests.exceptions.HTTPError as exc:
            if getattr(exc.response, 'status_code', None) in (401, 403):
                raise HTTPException(503, '识图服务凭据失效，请联系开通人员处理') from exc
            raise HTTPException(503, '识图服务暂时不可用，请稍后重试') from exc
        except (_requests.exceptions.RequestException, KeyError, IndexError) as exc:
            raise HTTPException(503, '识图服务暂时不可用，请稍后重试') from exc
        return {'hits': search.query(request_conn(), vec,
                                     top_k=body.top_k, exclude=body.exclude_ids)}

    # ---- 商品直写（H5用户本人操作=即审批，不走工单）----
    @app.post('/products/{category}/direct')
    def direct_create(category: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            from . import dynamic_catalog, inner_code as _ic
            try:
                template = dynamic_catalog.get_template(request_conn(), category)
            except KeyError:
                raise HTTPException(404, '未知品类')
            allowed = {field['key'] for field in template['fields']}
            data = {key: str(value) for key, value in body.changes.items() if key in allowed}
            if not data and body.images is None:
                raise HTTPException(400, 'changes 不能为空')
            visible = str(body.changes.get('cs_visible', '0')).strip()
            if visible not in {'0', '1'}:
                raise HTTPException(400, '可观测只能是 0 或 1')
            pid = secrets.token_hex(8)
            img_rels = []
            for fn in body.images or []:
                data_bytes = _read_image_source(fn)
                ext = os.path.splitext(fn)[1] or '.png'
                img_rels.append(app.state.storage.save(category, pid, f'img-{secrets.token_hex(8)}{ext}', data_bytes))
            result = dynamic_catalog.upsert_approved_products(request_conn(), category, [{
                'id': pid, 'inner_code': _ic.gen(), 'data': data, 'images': img_rels,
                'cs_visible': int(visible), 'row_fingerprint': '', 'source_row': None,
            }])
            request_conn().commit()
            if img_rels:
                from . import search
                search.reindex(request_conn(), app.state.storage, category)
            return {'id': pid, **result}
        import secrets as _sec
        from . import inner_code as _ic
        t = TEMPLATES[category]
        pid = _sec.token_hex(8)
        cols = list(body.changes.keys())
        if not cols:
            raise HTTPException(400, 'changes 不能为空')
        conn_cols = [c for c in cols if c in {*dict(t.fields), 'cs_visible'}]
        if not conn_cols:
            raise HTTPException(400, '没有合法字段')
        _check_cs_visible(category, None, body.changes)
        img_rels = []
        for fn in (body.images or []):
            data = _read_image_source(fn)
            ext = os.path.splitext(fn)[1] or '.png'
            img_rels.append(app.state.storage.save(category, pid, f'img-{secrets.token_hex(8)}{ext}', data))
        request_conn().execute(
            f"INSERT INTO {t.table}(id, inner_code, {', '.join(conn_cols)}, image_main, images) "
            f"VALUES({','.join('?' for _ in range(2 + len(conn_cols) + 2))})",
            (pid, _ic.gen(), *[str(body.changes[c]) for c in conn_cols],
             img_rels[0] if img_rels else '',
             json.dumps(img_rels, ensure_ascii=False) if img_rels else '[]'))
        request_conn().commit()
        from . import search
        if img_rels:
            search.reindex(request_conn(), app.state.storage, category)
        return {'id': pid, 'inner_code': request_conn().execute(
            f'SELECT inner_code FROM {t.table} WHERE id=?', (pid,)).fetchone()['inner_code']}

    @app.patch('/products/{category}/{pid}/direct')
    def direct_update(category: str, pid: str, body: MutateIn, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            from . import dynamic_catalog
            try:
                template = dynamic_catalog.get_template(request_conn(), category)
                row = next(value for value in dynamic_catalog.list_products(request_conn(), category)
                           if value['id'] == pid)
            except (KeyError, StopIteration):
                raise HTTPException(404, '商品不存在')
            allowed = {field['key'] for field in template['fields']}
            data = {**row['data'], **{key: str(value) for key, value in body.changes.items() if key in allowed}}
            visible = str(body.changes.get('cs_visible', row['cs_visible'])).strip()
            if visible not in {'0', '1'}:
                raise HTTPException(400, '可观测只能是 0 或 1')
            images = row['images']
            if body.images is not None:
                images = []
                for fn in body.images:
                    data_bytes = _read_image_source(fn)
                    ext = os.path.splitext(fn)[1] or '.png'
                    images.append(app.state.storage.save(category, pid, f'img-{secrets.token_hex(8)}{ext}', data_bytes))
            dynamic_catalog.upsert_approved_products(request_conn(), category, [{
                'id': pid, 'inner_code': row['inner_code'], 'data': data, 'images': images,
                'cs_visible': int(visible), 'status': row['status'],
                'source_row': row['source_row'], 'row_fingerprint': row['row_fingerprint'],
            }], source_key=row['source_key'], source_sheet=row['source_sheet'], source_doc=row['source_doc'])
            if body.images is not None:
                request_conn().execute('DELETE FROM embedding WHERE product_id=?', (pid,))
            request_conn().commit()
            if body.images is not None and images:
                from . import search
                search.reindex(request_conn(), app.state.storage, category)
            return {'updated': True}
        _check_cs_visible(category, pid, body.changes)
        t = TEMPLATES[category]
        if body.changes:
            conn_cols = [c for c in body.changes if c in {*dict(t.fields), 'cs_visible'}]
            sets = ', '.join(f'{c}=?' for c in conn_cols)
            if sets:
                request_conn().execute(f"UPDATE {t.table} SET {sets}, updated_at=datetime('now') WHERE id=?",
                             (*[str(body.changes[c]) for c in conn_cols], pid))
        if body.images is not None:
            img_rels = []
            for fn in body.images:
                data = _read_image_source(fn)
                ext = os.path.splitext(fn)[1] or '.png'
                img_rels.append(app.state.storage.save(category, pid, f'img-{secrets.token_hex(8)}{ext}', data))
            request_conn().execute(f"UPDATE {t.table} SET image_main=?, images=?, updated_at=datetime('now') WHERE id=?",
                         (img_rels[0] if img_rels else '', json.dumps(img_rels, ensure_ascii=False), pid))
            request_conn().execute('DELETE FROM embedding WHERE product_id=?', (pid,))
        request_conn().commit()
        from . import search
        search.reindex(request_conn(), app.state.storage, category)
        return {'updated': True}

    @app.delete('/products/{category}/{pid}/direct')
    def direct_delete(category: str, pid: str, request: Request):
        _auth(request, app.state.token)
        if category not in TEMPLATES:
            from . import dynamic_catalog
            try:
                dynamic_catalog.get_template(request_conn(), category)
            except KeyError:
                raise HTTPException(404, '未知品类')
            updated = request_conn().execute(
                "UPDATE product_dynamic SET status='delisted',updated_at=datetime('now') "
                'WHERE id=? AND category_key=?', (pid, category)).rowcount
            request_conn().execute('DELETE FROM embedding WHERE product_id=?', (pid,))
            request_conn().commit()
            if not updated:
                raise HTTPException(404, '商品不存在')
            return {'delisted': True}
        t = TEMPLATES[category]
        request_conn().execute(f"UPDATE {t.table} SET status='delisted', "
                     f"updated_at=datetime('now') WHERE id=?", (pid,))
        request_conn().commit()
        return {'delisted': True}

    # ---- 报价单（v2：多商品+数量+百分比调整）----
    class QuoteItem(BaseModel):
        category: str
        product_id: str
        quantity: int = Field(default=1, gt=0)

    class QuoteIn(BaseModel):
        items: list[QuoteItem]
        price_adjustment_pct: FiniteFloat = Field(default=0, ge=-100)
        deposit_pct: FiniteFloat = Field(default=30, ge=0, le=100)          # 定金百分比（30=30%），商家说"两成定金"传20

    @app.post('/quote')
    def do_quote(body: QuoteIn, request: Request):
        _auth(request, app.state.token)
        if not body.items:
            raise HTTPException(400, 'items 不能为空')
        import uuid
        from . import quote as quote_mod
        if not os.path.isfile(quote_mod.TEMPLATE_V2_PATH):
            raise HTTPException(503, '缺少商家真实报价模板，暂不能生成正式报价单')
        out_dir = os.path.join(app.state.storage.base, '_quotes')
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f'quote-{uuid.uuid4().hex[:8]}.xlsx')
        items = [{'category': i.category, 'product_id': i.product_id, 'quantity': i.quantity}
                 for i in body.items]
        try:
            quote_mod.generate_v2(request_conn(), app.state.storage, items,
                                  body.price_adjustment_pct, out, deposit_pct=body.deposit_pct)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        from . import notify
        notify.push_file(f'📄 报价单已生成（{len(items)} 款，调整 {body.price_adjustment_pct:+.0f}%）', out, conn=request_conn())
        return {'path': out}

    @app.get('/quote/{job_id}')
    def quote_status(job_id: str, request: Request):
        _auth(request, app.state.token)
        from . import quote as quote_mod
        try:
            return quote_mod.job_status(job_id)
        except KeyError:
            raise HTTPException(404, 'no such job')

    # ---- C端：红线知识（微信 AI 对话 → 工具 → 审批 → 生效）----
    class RedlineIn(BaseModel):
        model_config = {'extra':'forbid'}
        text_raw: str
        text_summary: str | None = None
        product_id: str | None = None

    @app.get('/shop')
    def shop_get(request: Request):
        _auth(request, app.state.token)
        from . import cs
        return cs.get_shop(request_conn())

    @app.get('/shop/linkage')
    def shop_linkage(request: Request):
        _auth(request, app.state.token)
        from .shop_link import profile
        p = profile(request_conn())
        from .templates import TEMPLATES
        from . import dynamic_catalog
        dynamic = dynamic_catalog.list_templates(request_conn())
        counts = {t.key: request_conn().execute(
            f"SELECT COUNT(*) FROM {t.table} WHERE COALESCE(NULLIF(status,''),'approved') != 'delisted'"
        ).fetchone()[0] for t in TEMPLATES.values()}
        visible = {t.key: request_conn().execute(
            f"SELECT COUNT(*) FROM {t.table} WHERE COALESCE(NULLIF(status,''),'approved') != 'delisted' AND cs_visible=1"
        ).fetchone()[0] for t in TEMPLATES.values()}
        dynamic = [template for template in dynamic if template['storage'] == 'dynamic']
        for template in dynamic:
            rows = dynamic_catalog.list_products(request_conn(), template['key'])
            rows = [row for row in rows if row['status'] != 'delisted']
            counts[template['key']] = len(rows)
            visible[template['key']] = sum(1 for row in rows if row['cs_visible'])
        return {'shop_id':p['shop_id'],'shop_name':p['shop_name'],
                'tg_bot_id':p['tg_bot_id'],'configured':bool(p['shop_name'] and p['tg_bot_id']),
                'catalog_counts': counts, 'customer_visible_counts': visible,
                'dynamic_categories': [{'key': t['key'], 'name': t['name'],
                                       'count': counts[t['key']],
                                       'customer_visible': visible[t['key']]}
                                      for t in dynamic],
                'mode':'one_shop_per_database'}

    @app.patch('/shop')
    def shop_update(body: MutateIn, request: Request):
        _auth(request, app.state.token)
        from . import cs
        try:
            changes = cs.validate_shop(body.changes)
            if cs.wechat_managed(request_conn()) and {'tg_bot_id', 'tg_bot_username'} & changes.keys():
                raise ValueError('请通过客服 Bot Token 接入入口绑定，不支持手填机器人身份')
            from .shop_link import validate_binding
            validate_binding(request_conn(),changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        tk = tickets.create(request_conn(), 'shop', None,
                            {'kind': 'shop', 'changes': changes, 'old': cs.get_shop(request_conn())})
        return {'ticket_id': tk['id'], 'token': tk['token'], 'status': 'pending'}

    @app.get('/cs/redline')
    def cs_redline_get(request: Request, product_id: str | None = None):
        _auth(request, app.state.token)
        from . import cs
        return {**cs.get_redline(request_conn(), product_id or None),
                'platform_rule':('' if cs.wechat_managed(request_conn()) else cs.SYSTEM_HARD_RULE),'platform_editable':False}

    @app.post('/cs/redline')
    def cs_redline_set(body: RedlineIn, request: Request):
        """AI 工具入口：只建审批工单，批准才生效（与改价格同款管道）。"""
        _auth(request, app.state.token)
        text_raw = body.text_raw.strip()
        from . import cs, tickets
        if not text_raw and not cs.wechat_managed(request_conn()):
            raise HTTPException(400, 'text_raw 不能为空')
        summary = (text_raw if cs.wechat_managed(request_conn()) else body.text_summary or cs.summarize(text_raw, llm=True))
        old = cs.get_redline(request_conn(), body.product_id or None)
        payload = {'kind': 'redline', 'product_id': body.product_id or None,
                   'text_raw': text_raw, 'text_summary': summary,
                   'old_text_raw': old['text_raw']}          # 审批卡显示旧文→新文
        t = tickets.create(request_conn(), 'redline', None, payload)
        _push_redline_card(request_conn(), t['id'], t['token'], body.product_id, old['text_raw'], text_raw)
        return {'ticket_id': t['id'], 'token': t['token'], 'status': 'pending_approval', 'notification': 'queued'}

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
        row = request_conn().execute('SELECT * FROM cs_link WHERE token=?', (token,)).fetchone()
        if row is None:
            raise HTTPException(404, '链接无效')
        active = request_conn().execute(
            "SELECT 1 FROM cs_link WHERE token=? AND datetime(expires_at)>datetime('now')",
            (token,)).fetchone()
        if not active:
            raise HTTPException(410, '链接已过期')
        return row

    @app.post('/cs/chat-token')
    def cs_chat_token(request: Request):
        """商家侧：生成/返回本店 H5 客服入口令牌（管理页按钮调用）。"""
        _auth(request, app.state.token)
        conn = request_conn()
        row = conn.execute('SELECT chat_token FROM shop_profile WHERE id=1').fetchone()
        token = (row['chat_token'] if row else '') or ''
        if not token:
            token = secrets.token_urlsafe(24)
            conn.execute('UPDATE shop_profile SET chat_token=? WHERE id=1', (token,))
            conn.commit()
        return {'chat_token': token}

    def _chat_conn(token: str):
        row = request_conn().execute('SELECT id FROM shop_profile WHERE chat_token=?', (token,)).fetchone()
        if row is None:
            raise HTTPException(404, '客服链接无效')
        return row

    @app.get('/cs/chat/{token}')
    def cs_chat_page(token: str):
        _chat_conn(token)
        return FileResponse(os.path.join(os.path.dirname(__file__), '..', 'static', 'cs', 'chat.html'))

    @app.post('/cs/chat/{token}/lang')
    def cs_chat_lang(token: str, body: dict, request: Request):
        from . import cs_chat
        _chat_conn(token)
        bot = cs_chat.H5Bot(request_conn(), api=None)
        cust = cs_chat.ensure_visitor(bot, str(body.get('visitor') or ''))
        return {'lang': cs_chat.set_language(request_conn(), cust, str(body.get('lang') or ''))}

    @app.post('/cs/chat/{token}/message')
    def cs_chat_message(token: str, body: dict, request: Request):
        from . import cs_chat
        _chat_conn(token)
        text = str(body.get('text') or '').strip()[:2000]
        if not text:
            raise HTTPException(400, '消息不能为空')
        bot = cs_chat.H5Bot(request_conn(), api=None)
        cust = cs_chat.ensure_visitor(bot, str(body.get('visitor') or ''))
        bot._processing = True
        bot._pending_catalog_photos = []
        try:
            reply = bot._on_text(cust, text)
        finally:
            bot._processing = False
        return {'reply': reply}

    @app.post('/cs/chat/{token}/photo')
    async def cs_chat_photo(token: str, request: Request):
        from . import cs_chat
        _chat_conn(token)
        form = await request.form()
        up = form.get('file')
        visitor = str(form.get('visitor') or '')
        if up is None or not hasattr(up, 'read'):
            raise HTTPException(400, '请上传 file 文件')
        data = await up.read()
        if not data:
            raise HTTPException(400, '文件为空')
        bot = cs_chat.H5Bot(request_conn(), api=None)
        cust = cs_chat.ensure_visitor(bot, visitor)
        bot._processing = True
        bot._pending_catalog_photos = []
        try:
            prepared = bot._prepare_photo(cust, data=data)
            reply = bot._on_photo(cust, None, prepared=prepared)
        finally:
            bot._processing = False
        return {'reply': reply}

    @app.get('/cs/link/{token}')
    def cs_link_view(token: str):
        from .shop_link import customer_fields
        row = _link_conn(token)
        notes = request_conn().execute(
            "SELECT * FROM cs_note WHERE customer_id=? AND status IN ('draft','confirmed') "
            'ORDER BY id', (row['customer_id'],)).fetchall()
        return {'notes': [{'id': n['id'], 'status': n['status'],
                           'photo': (f'/cs/link/{token}/note/{n["id"]}/photo' if n['photo'] else ''),
                           'fields': customer_fields(request_conn(),n)} for n in notes]}

    @app.get('/cs/link/{token}/note/{note_id}/photo')
    def cs_link_photo(token: str, note_id: int):
        link = _link_conn(token)
        row = request_conn().execute(
            "SELECT photo FROM cs_note WHERE id=? AND customer_id=? AND status IN ('draft','confirmed')",
            (note_id, link['customer_id'])).fetchone()
        if row is None or not row['photo'] or not os.path.isfile(row['photo']):
            raise HTTPException(404, 'no photo')
        return Response(content=open(row['photo'], 'rb').read(), media_type='image/jpeg',
                        headers={'Cache-Control': 'private, no-store'})

    @app.patch('/cs/link/{token}/note/{note_id}')
    async def cs_link_edit(token: str, note_id: int, request: Request):
        link = _link_conn(token)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, '请求必须为 JSON 对象')
        if not isinstance(body, dict):
            raise HTTPException(400, '请求必须为 JSON 对象')
        field, value = str(body.get('field', '')), str(body.get('value', ''))
        if not field:
            raise HTTPException(400, 'field 不能为空')
        row = request_conn().execute(
            "SELECT * FROM cs_note WHERE id=? AND customer_id=? AND status IN ('draft','confirmed')",
            (note_id, link['customer_id'])).fetchone()
        if row is None:
            raise HTTPException(404, '条目不存在')
        from . import shop_link
        try:
            shop_link.set_field(request_conn(),row,field,value)
        except ValueError as exc:
            raise HTTPException(400,str(exc))
        request_conn().commit()
        updated=request_conn().execute('SELECT * FROM cs_note WHERE id=?',(note_id,)).fetchone()
        return {'saved': True, 'fields': shop_link.customer_fields(request_conn(),updated)}

    @app.get('/cs/link/{token}/export.xlsx')
    def cs_link_export(token: str):
        row = _link_conn(token)
        notes = request_conn().execute(
            "SELECT * FROM cs_note WHERE customer_id=? AND status IN ('draft','confirmed') ORDER BY id",
            (row['customer_id'],)).fetchall()
        from .cs_export import render_notes
        if not notes:
            raise HTTPException(409, '暂无可导出条目，请先上传采购照片')
        from .shop_link import snapshot
        lang = request_conn().execute(
            'SELECT lang FROM cs_customer WHERE id=?', (row['customer_id'],)).fetchone()
        lang = (lang[0] if lang and lang[0] else '') or ''
        from . import llm as _llm, cs_i18n
        # 请求线程内同步翻译：未缓存条数超限直接回退中文表，别把 HTTP 请求拖死。
        def _texts(texts, **kw):
            return cs_i18n.translate_texts(request_conn(), _llm, lang, texts, max_missing=200, **kw)
        content = render_notes([snapshot(request_conn(),n) for n in notes], include_status=True,
                               lang=lang, conn=request_conn(), llm=_llm,
                               texts=_texts if lang and lang != '中文' else None)
        title = _texts(['采购清单'])[0] if lang and lang != '中文' else '采购清单'
        from urllib.parse import quote
        fname = quote(f"{title}-{row['customer_id'][:6]}.xlsx")
        return Response(content=content,
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})
