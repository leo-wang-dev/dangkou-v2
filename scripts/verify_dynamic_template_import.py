"""Verify a real merchant workbook through import, approval and TG catalog delivery.

The run is isolated: it creates a temporary database and image store, replaces the
embedding provider with a deterministic local vector, and never contacts Telegram.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from catalog import config, db, dynamic_catalog, merchant_policy, photo_inquiry, price_policy, search, tickets
from catalog.csbot import CsBot
from catalog.dynamic_import import build_ticket_payload
from catalog.main import app
from catalog.storage import LocalStorage


class CaptureTelegram:
    def __init__(self):
        self.messages = []
        self.photos = []
        self.documents = []

    def send_message(self, chat_id, text):
        self.messages.append({'chat_id': chat_id, 'text': text})

    def send_photo(self, chat_id, filename, content, caption=''):
        self.photos.append({'chat_id': chat_id, 'filename': filename,
                            'bytes': len(content), 'caption': caption})

    def send_document(self, chat_id, filename, content, caption=''):
        self.documents.append({'chat_id': chat_id, 'filename': filename,
                               'bytes': len(content), 'caption': caption})


class NoNetworkModel:
    def chat_text(self, system, messages, **kwargs):
        if '采购记录抽取' in system:
            return '{"actions":[]}'
        if '规则匹配' in system:
            return 'PASS'
        return '{"action":"none"}'


def _local_embedding(content: bytes) -> list[float]:
    digest = hashlib.sha256(content).digest()
    return [value / 255 for value in digest[:8]]


def run(workbook: Path, output: Path) -> dict:
    workbook = workbook.resolve()
    if not workbook.is_file():
        raise FileNotFoundError(workbook)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dynamic-import-audit-') as folder:
        root = Path(folder)
        work = root / 'extracted'
        storage = LocalStorage(str(root / 'storage'))
        conn = db.connect(str(root / 'shop.db'))
        db.init_db(conn)
        conn.execute("UPDATE shop_profile SET shop_name='吹风机测试档口',tg_bot_id='999999',"
                     "owner_wechat='TEST-WECHAT' WHERE id=1")
        merchant_policy.apply(conn, {'shop_name': '吹风机测试档口', 'wechat_managed': True}, 1)
        conn.commit()

        payload = build_ticket_payload(conn, workbook, work,
                                       source_key='real-workbook-acceptance', doc_id=1)
        ticket = tickets.create(conn, 'template_import', None, payload)
        app.state.conn = conn
        app.state.token = 'local-audit-token'
        app.state.storage = storage
        app.state.callback = None
        search.embed_image = _local_embedding
        os.environ['CATALOG_V2_IMG'] = str(storage.base)
        with TestClient(app, headers={'X-Service-Token': app.state.token}) as client:
            decision = client.post(f"/tickets/{ticket['id']}/decision", json={
                'token': ticket['token'], 'approved': True,
            })
            if decision.status_code != 200:
                raise AssertionError(decision.text)
            section = payload['sheets'][0]
            category = section['template']['key']
            downloaded = client.get(f'/categories/{category}/template.xlsx')
            if downloaded.status_code != 200:
                raise AssertionError(downloaded.text)
            (output / '吹风机-标准导入模板.xlsx').write_bytes(downloaded.content)

        template = dynamic_catalog.get_template(conn, category)
        products = dynamic_catalog.list_products(conn, category)
        public = dynamic_catalog.list_products(conn, category, public_only=True)
        model_key = next(field['key'] for field in template['fields'] if field['role'] == 'model')
        models = [row['data'].get(model_key, '') for row in products]
        model_counts = Counter(models)

        assert len(payload['sheets']) == 1
        assert template['name'] == '吹风机'
        assert section['header_row'] == 2
        expected_rows = 8
        assert section['image_count'] >= 0
        assert len(template['fields']) == 11
        assert len(products) == expected_rows
        expected_blank_models = 3
        assert sum(not value for value in models) == expected_blank_models
        assert model_counts['戴森款HD15'] == 2
        assert len({row['id'] for row in products}) == len(products)
        assert all(row['status'] == 'approved' and row['cs_visible'] == 1 for row in products)
        assert all('成本' not in str(row) and not price_policy.contains_link(str(row))
                   and not price_policy.contains_price_amount(str(row)) for row in public)

        first_with_image = next(row for row in products if row['image_main'])
        config.BAILIAN_API_KEY = 'local-audit-only'
        photo_candidates = photo_inquiry.local_candidates(
            conn, [{'型号或品名': '模糊'}], storage.read(first_with_image['image_main']))
        assert 1 <= len(photo_candidates) <= 3
        assert any(item['product_id'] == first_with_image['id'] for item in photo_candidates)

        transport = CaptureTelegram()
        bot = CsBot(conn, transport, llm=NoNetworkModel(), img_dir=str(root / 'customer-photos'))
        bot.handle_update({'update_id': 1, 'message': {
            'chat': {'id': 100, 'type': 'private'}, 'from': {'id': 100},
            'text': '查询商品',
        }})
        reply = transport.messages[-1]['text']
        assert 1 <= len(transport.photos) <= 3
        assert '成本' not in reply and not price_policy.contains_link(reply)
        assert not price_policy.contains_price_amount(reply)
        assert all(photo['bytes'] > 0 for photo in transport.photos)

        evidence = {
            'workbook': str(workbook),
            'sheet_count': len(payload['sheets']),
            'category': template['name'],
            'category_key': category,
            'header_row': section['header_row'],
            'field_count': len(template['fields']),
            'image_count': section['image_count'],
            'product_count': len(products),
            'blank_model_count': expected_blank_models,
            'duplicate_hd15_count': model_counts['戴森款HD15'],
            'public_product_count': len(public),
            'embedding_count': conn.execute(
                'SELECT count(*) FROM embedding WHERE category=?', (category,)).fetchone()[0],
            'photo_candidate_count': len(photo_candidates),
            'photo_hit_product_id': first_with_image['id'],
            'tg_reply': reply,
            'tg_photo_count': len(transport.photos),
            'tg_photo_bytes': [photo['bytes'] for photo in transport.photos],
            'template_download': str(output / '吹风机-标准导入模板.xlsx'),
            'passed': True,
        }
        conn.close()
    (output / 'result.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--output', type=Path, default=Path('audit/round20/dynamic-template'))
    args = parser.parse_args()
    result = run(args.workbook, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
