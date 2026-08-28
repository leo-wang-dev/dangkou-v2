#!/usr/bin/env python3
"""检索质量测评：逐商品用其主图查一次，统计 Top1/Top3 命中率（PRD 线：80%/95%）。"""
import json
import os
import sqlite3
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8890'
TOKEN = os.environ.get('CATALOG_V2_SERVICE_TOKEN', '')
DB = os.environ.get('CATALOG_V2_DB', os.path.join(
    os.path.dirname(__file__), '..', 'data', 'catalog.db'))
IMG = os.path.join(os.path.dirname(DB), 'images')


def search(image_path):
    req = urllib.request.Request(
        BASE + '/search', method='POST',
        data=json.dumps({'image_path': image_path, 'top_k': 5}).encode(),
        headers={'X-Service-Token': TOKEN, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())['hits']


conn = sqlite3.connect(DB, check_same_thread=False)
conn.row_factory = sqlite3.Row
pools = {'razor': conn.execute("SELECT id, image_main FROM product_razor "
                               "WHERE image_main != '' AND status != 'delisted'").fetchall(),
         'curler': conn.execute("SELECT id, image_main FROM product_curler "
                                "WHERE image_main != '' AND status != 'delisted'").fetchall()}
top1 = top3 = total = 0
for cat, rows in pools.items():
    for r in rows:
        p = os.path.join(IMG, r['image_main'])
        if not os.path.exists(p):
            continue
        hits = search(p)
        total += 1
        ids = [h['product_id'] for h in hits]
        if ids[:1] and ids[0] == r['id']:
            top1 += 1
        if r['id'] in ids[:3]:
            top3 += 1
        print(f'[{cat}] {r["id"]}: '
              f'rank={ids.index(r["id"]) + 1 if r["id"] in ids else "-"}', flush=True)
r1 = top1 * 100 // max(total, 1)
r3 = top3 * 100 // max(total, 1)
print(f'\nTop1 {top1}/{total} = {r1}%  |  Top3 {top3}/{total} = {r3}%')
print('达标' if (r1 >= 80 and r3 >= 95)
      else '不达标 → 优化路线：text_vec 融合 / LLM rerank')
