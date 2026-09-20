"""Offline full-chain verification for the WeChat merchant assistant capabilities."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import time
from pathlib import Path

import openpyxl
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import db, search
from catalog.api import register_routes
from catalog.storage import LocalStorage
from scripts.build_test_quote_template import build as build_quote_template
from scripts.build_wechat_manual_test_kit import OUT as KIT, main as build_kit


OUT = ROOT / 'audit' / 'round21' / 'wechat-verification'
TOKEN = 'offline-wechat-verification'


def embedding(data: bytes, *_args) -> list[float]:
    """Deterministic local vector: identical image bytes must rank first."""
    digest = hashlib.sha256(data).digest()
    return [(value - 127.5) / 127.5 for value in digest]


def wait_import(client: TestClient, doc_id: int) -> dict:
    for _ in range(250):
        value = client.get(f'/import/{doc_id}').json()
        if value['status'] != 'parsing':
            return value
        time.sleep(.02)
    raise AssertionError(f'import {doc_id} did not finish')


def main() -> None:
    build_kit()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    conn = db.connect(str(OUT / 'catalog.db'))
    db.init_db(conn)
    storage = LocalStorage(str(OUT / 'images'))
    app = FastAPI()
    app.state.conn = conn
    app.state.token = TOKEN
    app.state.storage = storage
    app.state.callback = None
    register_routes(app)
    original_embed = search.embed_image
    search.embed_image = embedding
    evidence: list[dict] = []

    def record(case_id: str, capability: str, kind: str, detail: str) -> None:
        evidence.append({'id': case_id, 'capability': capability,
                         'kind': kind, 'status': 'passed', 'detail': detail})

    try:
        with TestClient(app, headers={'X-Service-Token': TOKEN}) as client:
            assert client.get('/stats', headers={'X-Service-Token': 'wrong'}).status_code == 401
            record('AUTH-NEG', '所有管理能力', 'negative', '无服务身份读取统计返回 401')

            primary = str(KIT / '微信正向-吹风机.xlsx')
            started = client.post('/import', json={'path': primary, 'source_key': 'wechat-round21-main'})
            assert started.status_code == 200
            doc_id = started.json()['doc_id']
            state = wait_import(client, doc_id)
            assert state['status'] == 'ticketed' and state['stats']['new'] == 4
            ticket = conn.execute(
                "SELECT * FROM approval_ticket WHERE ticket_type='template_import' "
                "AND json_extract(payload,'$.doc_id')=?", (doc_id,)).fetchone()
            assert ticket is not None
            assert conn.execute('SELECT count(*) FROM product_dynamic').fetchone()[0] == 0
            record('EXCEL-PRE', 'Excel 导入', 'positive', '识别 1 个 Sheet/4 行；审批前商品库仍为 0')

            approved = client.post(f'/tickets/{ticket["id"]}/decision', json={
                'token': ticket['token'], 'approved': True})
            assert approved.status_code == 200, approved.text
            category = conn.execute(
                "SELECT key FROM category_template WHERE name='微信验收吹风机'").fetchone()[0]
            stats = client.get(f'/stats?category={category}&full=true').json()
            rows = stats['products'][category]
            assert stats['total'] == 4 and [row['产品型号'] for row in rows] == [
                'WX-HD15', 'WX-HD15', 'WX-HD16', '']
            assert all(row['可观测'] == 1 for row in rows)
            assert all(row['主图'] for row in rows)
            record('EXCEL-OK', 'Excel 导入', 'positive',
                   '批准后 4 行和 4 张图入库；重复型号、空型号均保留；默认可见')

            replay = client.post(f'/tickets/{ticket["id"]}/decision', json={
                'token': ticket['token'], 'approved': True})
            assert replay.status_code == 400
            assert client.get(f'/stats?category={category}').json()['total'] == 4
            record('EXCEL-REPLAY', 'Excel 导入', 'negative', '重复批准被拒绝且总数仍为 4')

            second = client.post('/import', json={
                'path': str(KIT / '微信并发-配件.xlsx'), 'source_key': 'wechat-round21-reject'}).json()
            assert wait_import(client, second['doc_id'])['status'] == 'ticketed'
            second_ticket = conn.execute(
                "SELECT * FROM approval_ticket WHERE ticket_type='template_import' "
                "AND json_extract(payload,'$.doc_id')=?", (second['doc_id'],)).fetchone()
            rejected = client.post(f'/tickets/{second_ticket["id"]}/decision', json={
                'token': second_ticket['token'], 'approved': False})
            assert rejected.status_code == 200
            assert conn.execute("SELECT 1 FROM category_template WHERE name='微信验收配件'").fetchone() is None
            record('EXCEL-REJECT', 'Excel 导入', 'negative', '驳回后分类和商品均未落库')

            broken = client.post('/import', json={
                'path': str(KIT / '反向-损坏Excel.xlsx'), 'source_key': 'wechat-round21-broken'}).json()
            broken_state = wait_import(client, broken['doc_id'])
            assert broken_state['status'] == 'failed' and broken_state['error']
            record('EXCEL-BROKEN', 'Excel 导入', 'negative', '损坏 xlsx 明确失败且没有审批工单')

            known = (KIT / 'WX-HD15-red.jpg').read_bytes()
            uploaded = client.post('/upload', files={'file': ('known.bin', known, 'application/octet-stream')})
            assert uploaded.status_code == 200 and uploaded.json()['path'].endswith('.jpg')
            fake = client.post('/upload', files={
                'file': ('fake.png', (KIT / '反向-伪装图片.png').read_bytes(), 'image/png')})
            assert fake.status_code == 400
            record('PHOTO-UPLOAD', '产品图', 'positive/negative', '真实 JPEG 按内容识别；伪装 PNG 在暂存前被拒绝')

            found = client.post('/search', json={
                'image_path': str(KIT / 'WX-HD15-red.jpg'), 'top_k': 3})
            assert found.status_code == 200 and found.json()['hits']
            top = found.json()['hits'][0]
            assert top['fields']['产品型号'] == 'WX-HD15' and top['fields']['颜色'] == '红色'
            excluded = client.post('/search', json={
                'image_path': str(KIT / 'WX-HD15-red.jpg'), 'top_k': 3,
                'exclude_ids': [top['product_id']]})
            assert all(hit['product_id'] != top['product_id'] for hit in excluded.json()['hits'])
            record('PHOTO-SEARCH', '产品图', 'positive', '相同图片命中正确商品；换一批排除首个商品 ID')

            missing = client.post('/search', json={
                'image_path': str(OUT / 'missing.jpg'), 'top_k': 3})
            invalid_k = client.post('/search', json={
                'image_path': str(KIT / 'WX-HD15-red.jpg'), 'top_k': 0})
            assert missing.status_code == 404 and invalid_k.status_code == 422
            record('PHOTO-SEARCH-NEG', '产品图', 'negative', '缺失图片和非法 top_k 均在检索前失败')

            template = client.get(f'/products/{category}').json()['template']
            keys = {field['label']: field['col'] for field in template['fields']}
            created = client.post(f'/products/{category}', json={
                'changes': {keys['产品型号']: 'WX-NEW-01', keys['颜色']: '绿色',
                            keys['功率']: '1400W', 'cs_visible': '0'},
                'images': [uploaded.json()['path']]})
            assert created.status_code == 200, created.text
            assert client.get(f'/stats?category={category}').json()['total'] == 4
            created_ticket = created.json()
            assert client.post(f'/tickets/{created_ticket["ticket_id"]}/decision', json={
                'token': created_ticket['token'], 'approved': True}).status_code == 200
            current = client.get(f'/products/{category}').json()['products']
            new = next(row for row in current if row['产品型号'] == 'WX-NEW-01')
            assert new['可观测'] == 0 and new['主图']
            record('PHOTO-CREATE', '产品图', 'positive', '带图新增审批前无变化；批准后入库且保持不可观测')

            unknown_field = client.post(f'/products/{category}', json={
                'changes': {'不存在字段': '值'}})
            assert unknown_field.status_code == 400
            record('PHOTO-CREATE-NEG', '产品图', 'negative', '模板外字段被拒绝，没有空商品工单')

            count = client.get(f'/stats?category={category}').json()
            full = client.get(f'/stats?category={category}&full=true').json()
            unknown_category = client.get('/stats?category=not_exists')
            assert count['total'] == 5 and len(full['products'][category]) == 5
            assert unknown_category.status_code == 404
            record('QUERY-OK', '商品查询', 'positive/negative', '实时款数=5、全量=5；未知分类返回 404')

            before = client.get('/shop').json()
            pending = client.patch('/shop', json={'changes': {
                'shop_name': '微信验收档口', 'owner_tg_username': '@wx_test_owner',
                'owner_wechat': 'WX-TEST-OWNER'}}).json()
            assert client.get('/shop').json()['shop_name'] == before['shop_name']
            assert client.post(f'/tickets/{pending["ticket_id"]}/decision', json={
                'token': pending['token'], 'approved': False}).status_code == 200
            assert client.get('/shop').json()['shop_name'] == before['shop_name']
            approved_contact = client.patch('/shop', json={'changes': {
                'shop_name': '微信验收档口', 'owner_tg_username': '@wx_test_owner',
                'owner_wechat': 'WX-TEST-OWNER'}}).json()
            assert client.post(f'/tickets/{approved_contact["ticket_id"]}/decision', json={
                'token': approved_contact['token'], 'approved': True}).status_code == 200
            shop = client.get('/shop').json()
            assert shop['shop_name'] == '微信验收档口'
            assert shop['owner_tg_username'] == 'wx_test_owner' and shop['owner_wechat'] == 'WX-TEST-OWNER'
            record('CONTACT-OK', '档口资料', 'positive', '审批前/驳回不变；批准后名称、TG、微信一起生效')

            assert client.patch('/shop', json={'changes': {}}).status_code == 400
            assert client.patch('/shop', json={'changes': {
                'owner_tg_username': 'invalid/url'}}).status_code == 400
            assert client.patch('/shop', json={'changes': {
                'owner_wechat': 'bad\nwechat'}}).status_code == 400
            record('CONTACT-NEG', '档口资料', 'negative', '空操作、非法 TG、带换行微信号均未建工单')

            from catalog import quote as quote_mod
            quote_template = OUT / 'test-quote-template.xlsx'
            build_quote_template(quote_template)
            quote_mod.TEMPLATE_V2_PATH = str(quote_template)
            quote_photo = storage.save('curler', 'quote-p1', 'product.jpg', known)
            conn.execute("INSERT INTO product_curler(id,inner_code,item_no,price,ctn_qty,ctn_size,"
                         "image_main,status,cs_visible) VALUES(?,?,?,?,?,?,?,?,?)",
                         ('quote-p1', 'WX-Q-1', 'WX-CURL-Q1', '10', '40', '50*40*30',
                          quote_photo, 'approved', 1))
            conn.commit()
            quotation = client.post('/quote', json={'items': [{
                'category': 'curler', 'product_id': 'quote-p1', 'quantity': 81}],
                'price_adjustment_pct': 0, 'deposit_pct': 20})
            assert quotation.status_code == 200, quotation.text
            quote_path = Path(quotation.json()['path'])
            sheet = openpyxl.load_workbook(quote_path).active
            assert sheet['F18'].value == 120 and sheet['G18'].value == 1200
            queued = conn.execute(
                "SELECT body FROM cs_outbox WHERE channel='notify_file' ORDER BY id DESC").fetchone()
            assert queued and json.loads(queued['body'])['file_path'] == str(quote_path)
            record('QUOTE-OK', '商家正式报价单', 'positive', '81 个按 40/箱进位为 120；文件可读并进入文件发送队列')

            assert client.post('/quote', json={'items': []}).status_code == 400
            assert client.post('/quote', json={'items': [{
                'category': 'curler', 'product_id': 'quote-p1', 'quantity': 0}]}).status_code == 422
            assert client.post('/quote', json={'items': [{
                'category': 'curler', 'product_id': 'missing', 'quantity': 1}]}).status_code == 400
            assert client.post('/quote', json={'items': [{
                'category': category, 'product_id': rows[0]['id'], 'quantity': 1}]}).status_code == 400
            record('QUOTE-NEG', '商家正式报价单', 'negative',
                   '空商品、0 数量、不存在商品、未配置映射的动态分类均不生成文件')

        result = {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'mode': 'offline-real-api-sqlite-excel-images-no-external-network',
            'passed': len(evidence), 'failed': 0, 'cases': evidence,
        }
        (OUT / 'result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        search.embed_image = original_embed
        conn.close()


if __name__ == '__main__':
    main()
