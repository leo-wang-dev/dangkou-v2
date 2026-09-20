"""Verify the deployed merchant database copy without touching live customer data."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import openpyxl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
SOURCE_DB = HERE / 'catalog.db'
RUN_DB = HERE / 'run.db'
shutil.copy2(SOURCE_DB, RUN_DB)
os.environ['CATALOG_V2_DB'] = str(RUN_DB)
os.environ['CATALOG_V2_IMG'] = str(HERE / 'images')
os.environ['CATALOG_V2_SERVICE_TOKEN'] = 'round23-copy-only'
os.environ.pop('CATALOG_CS_API_URL', None)
os.environ.pop('CATALOG_CS_SERVICE_TOKEN', None)

from catalog import db, dynamic_catalog
from catalog.csbot import CsBot


class Model:
    def chat_text(self, system, messages, **kwargs):
        if '采购记录抽取' in system:
            return '{"actions":[]}'
        if '规则匹配' in system:
            return 'PASS'
        return '{"action":"none"}'


api = SimpleNamespace(sent=[], documents=[], photos=[], download_photo=lambda _: b'')
api.send_message = lambda *args: api.sent.append(args)
api.send_document = lambda *args: api.documents.append(args)
api.send_photo = lambda *args: api.photos.append(args)
conn = db.connect(str(RUN_DB))
db.init_db(conn)
bot = CsBot(conn, api, llm=Model(), img_dir=str(HERE / 'customer-photos'))
customer = 9230001


def send(text, uid):
    bot.handle_update({'update_id': uid, 'message': {
        'from': {'id': customer}, 'chat': {'id': customer, 'type': 'private'}, 'text': text}})


send('WX-HD15 有哪些颜色', 1)
variant_reply = api.sent[-1][1]
assert '红色' in variant_reply and '蓝色' in variant_reply
assert '价格' not in variant_reply and '成本' not in variant_reply

send('我想采购100台wx-hd16', 2)
send('我想采购20套档口没有的定制礼盒', 3)

template = next(t for t in dynamic_catalog.list_templates(conn) if t['storage'] == 'dynamic')
power_key = next(f['key'] for f in template['fields'] if f['label'] == '功率')
row = conn.execute("SELECT id,data_json FROM product_dynamic WHERE data_json LIKE '%WX-HD16%'").fetchone()
data = json.loads(row['data_json'])
data[power_key] = '1900W（出表前更新）'
conn.execute('UPDATE product_dynamic SET data_json=? WHERE id=?',
             (json.dumps(data, ensure_ascii=False), row['id']))
conn.commit()

send('出表', 4)
content = api.documents[-1][2]
output = HERE / '采购笔记-线上副本增量验证.xlsx'
output.write_bytes(content)
sheet = openpyxl.load_workbook(io.BytesIO(content)).active
rows = list(sheet.values)
headers = list(rows[0])
assert '商品编号' not in headers and '商品类别' not in headers
assert any('WX-HD16' in r and '1900W（出表前更新）' in r for r in rows[1:])
assert any('档口没有的定制礼盒' in r for r in rows[1:])

customer_id = conn.execute('SELECT id FROM cs_customer WHERE tg_id=?',
                           (str(customer),)).fetchone()['id']
link = conn.execute(
    'SELECT token FROM cs_link WHERE customer_id=? ORDER BY rowid DESC LIMIT 1',
    (customer_id,)).fetchone()['token']
from fastapi.testclient import TestClient
from catalog.main import app
response = TestClient(app).get('/cs/link/' + link)
assert response.status_code == 200
payload = response.json()
serialized = json.dumps(payload, ensure_ascii=False)
for forbidden in ('customer_id', 'received_shop_id', 'source_shop_id', '商品编号', '商品类别'):
    assert forbidden not in serialized

result = {
    'database_integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
    'duplicate_model_reply': variant_reply,
    'excel_rows': sheet.max_row - 1,
    'excel_headers': headers,
    'excel_images': len(sheet._images),
    'export_refreshed_latest_catalog_value': '1900W（出表前更新）' in str(rows),
    'unmatched_item_preserved': '档口没有的定制礼盒' in str(rows),
    'customer_link_internal_fields_hidden': True,
    'passed': True,
}
(HERE / 'verification-result.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
