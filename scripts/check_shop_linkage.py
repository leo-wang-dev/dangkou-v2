"""Read-only comparison of the bot database and the merchant API, before deployment."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config


def compare(local, remote):
    if not local.get('shop_name') or not local.get('tg_bot_id'):
        raise ValueError('本地库尚未审批档口名称和 bot 绑定')
    if not remote.get('configured') or any(local.get(k)!=remote.get(k) for k in ('shop_id','shop_name','tg_bot_id')):
        raise ValueError('商家 API 与 bot 数据库的档口身份或绑定不一致，不能作为联动环境')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True,help='Merchant API URL; service token comes from environment')
    args=parser.parse_args()
    import requests
    try:
        path=Path(config.DB_PATH).resolve()
        conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
        try:local=dict(conn.execute('SELECT shop_id,shop_name,tg_bot_id FROM shop_profile WHERE id=1').fetchone())
        finally:conn.close()
        response=requests.get(args.base_url.rstrip('/')+'/shop/linkage',headers={'X-Service-Token':config.SERVICE_TOKEN},timeout=15)
        response.raise_for_status();remote=response.json();compare(local,remote)
        print(json.dumps({'matched':True,'shop_id':local['shop_id'],'shop_name':local['shop_name'],
                          'catalog_counts':remote['catalog_counts'],
                          'customer_visible_counts':remote.get('customer_visible_counts', {}),
                          'dynamic_categories':remote.get('dynamic_categories', [])},ensure_ascii=False))
    except ValueError as exc:
        # Do not print HTTP response bodies or credential-bearing URLs.
        print('联动检查失败：身份/配置或响应格式不匹配。');return 1
    except Exception as exc:
        print('联动检查失败：'+type(exc).__name__);return 1
    return 0


if __name__=='__main__':sys.exit(main())
