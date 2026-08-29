import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, db, notify
from .api import register_routes
from .storage import LocalStorage

app = FastAPI(title='dangkou catalog v2')
app.state.conn = db.connect()
db.init_db(app.state.conn)
app.state.token = config.SERVICE_TOKEN
app.state.storage = LocalStorage(config.IMG_DIR)
app.state.callback = notify.push  # 解析完成 → 回流引擎 → 微信主动推送


@app.middleware('http')
async def no_cache_html(request, call_next):
    r = await call_next(request)
    if 'text/html' in r.headers.get('content-type', ''):
        r.headers['Cache-Control'] = 'no-cache'  # H5 发版即生效（旧JS缓存事故）
    return r


register_routes(app)
_static = os.path.join(os.path.dirname(__file__), '..', 'static')
if os.path.isdir(_static):
    app.mount('/', StaticFiles(directory=_static, html=True), name='static')
