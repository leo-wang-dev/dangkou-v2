#!/usr/bin/env python3
"""模板批准后自动驱动商品阶段（演示期守护）。

每 15 秒扫一次 import_doc：发现 phase=template 且 status=template_approved
且尚无对应 products 单的，就按引擎同款协议 POST /import 续跑商品解析。
幂等：已存在任何 products 单（无论状态）的模板不再重跑。
日志：/tmp/import-continuer.log
"""
import json
import sqlite3
import time
import urllib.request

DB = '/home/ubuntu/dangkou-v2/data/catalog.db'
API = 'http://127.0.0.1:8890'
TOKEN = open('/home/ubuntu/dangkou-v2/.env').read().split('CATALOG_V2_SERVICE_TOKEN=', 1)[1].split('\n', 1)[0].strip()
LOG = open('/tmp/import-continuer.log', 'a', 1)


def log(msg):
    LOG.write(f'{time.strftime("%H:%M:%S")} {msg}\n')


def post(doc):
    body = json.dumps({'path': doc['input_path'], 'phase': 'products',
                       'template_doc_id': doc['id']}).encode()
    req = urllib.request.Request(API + '/import', data=body, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'X-Service-Token': TOKEN})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


while True:
    try:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        done = {r['template_doc_id'] for r in
                con.execute("SELECT DISTINCT template_doc_id FROM import_doc WHERE phase='products'")}
        for doc in con.execute(
                "SELECT id,input_path,filename FROM import_doc "
                "WHERE phase='template' AND status='template_approved'"):
            if doc['id'] in done:
                continue
            r = post(doc)
            log(f"续跑商品阶段: 模板#{doc['id']} ({doc['filename']}) → doc {r.get('doc_id')}")
        con.close()
    except Exception as exc:
        log(f'error: {exc}')
    time.sleep(15)
