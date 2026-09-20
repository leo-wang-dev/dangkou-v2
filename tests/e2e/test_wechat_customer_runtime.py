import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from catalog import db,merchant_policy
from catalog.merchant_binding import write_secret
from tests.e2e.test_merchant_runtime import wait_for


def test_single_shop_runtime_start_rotate_preserves_wechat_info(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            result={'id':222222,'username':'customer_bot','is_bot':True} if self.path.endswith('getMe') else []
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'ok':True,'result':result}).encode())
    tg=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=tg.serve_forever,daemon=True).start()
    c=db.connect(str(tmp_path/'shop.db'));db.init_db(c)
    c.execute("UPDATE shop_profile SET shop_name='微信档口',tg_bot_id='222222',owner_wechat='owner-after-edit' WHERE id=1")
    merchant_policy.apply(c,{'wechat_managed':True},1);c.commit()
    runtime=tmp_path/'runtime';write_secret(runtime/'credentials.json',{'bot_id':'222222','bot_token':'222222:test'})
    env={**os.environ,'CATALOG_V2_DB':str(tmp_path/'shop.db'),'WECHAT_CUSTOMER_STATE':str(runtime),
         'TG_API_BASE':f'http://127.0.0.1:{tg.server_port}','TG_PROXY_URL':'','CATALOG_CS_API_URL':''}
    project=Path(__file__).resolve().parents[2]
    with open(tmp_path/'runtime.log','w') as log:
        proc=subprocess.Popen([sys.executable,'scripts/run_wechat_customer.py'],cwd=project,env=env,stdout=log,stderr=log)
        try:
            def status():
                try:return json.loads((runtime/'status.json').read_text())
                except (OSError,ValueError):return {}
            wait_for(lambda: status().get('runtime_status')=='running')
            pid=status()['pid']
            write_secret(runtime/'credentials.json',{'bot_id':'222222','bot_token':'222222:rotated'})
            wait_for(lambda: status().get('runtime_status')=='running' and status().get('pid')!=pid)
            assert c.execute('SELECT owner_wechat FROM shop_profile').fetchone()[0]=='owner-after-edit'
            assert merchant_policy.read(c)=={'wechat_managed':True}
        finally:
            proc.terminate();proc.wait(timeout=15);tg.shutdown();c.close()
