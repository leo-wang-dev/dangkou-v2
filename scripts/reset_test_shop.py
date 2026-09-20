"""Reset one isolated shop to a zero-data acceptance state.

The command makes a 0600 SQLite backup first and requires an explicit shop id
when the database already has one.  WeChat binding files are opt-in because a
machine may host more than one shop.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time


PRODUCT_TABLES = ('product_razor', 'product_curler', 'product_dynamic', 'embedding', 'embedding_retry')
SESSION_TABLES = (
    'approval_ticket', 'import_doc', 'cs_customer', 'cs_note', 'cs_conversation_log',
    'cs_link', 'cs_inbox', 'cs_outbox', 'cs_context', 'cs_photo_candidates',
    'cs_redline', 'merchant_policy',
)


def reset(database: Path, *, expected_shop_id: str | None, wechat_binding: Path | None,
          image_dir: Path | None) -> dict:
    database = database.resolve()
    if not database.is_file():
        raise SystemExit(f'数据库不存在：{database}')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = database.with_name(database.name + f'.before-reset-{stamp}')
    shutil.copy2(database, backup)
    os.chmod(backup, 0o600)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        profile = conn.execute('SELECT * FROM shop_profile WHERE id=1').fetchone()
        if profile is None:
            raise SystemExit('数据库缺少 shop_profile，拒绝执行')
        shop_id = profile['shop_id']
        if expected_shop_id and shop_id != expected_shop_id:
            raise SystemExit(f'档口身份不匹配：期望 {expected_shop_id}，实际 {shop_id}')
        counts = {}
        for table in PRODUCT_TABLES + SESSION_TABLES:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                counts[table] = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                conn.execute(f'DELETE FROM {table}')
        conn.execute("DELETE FROM category_template WHERE storage='dynamic'")
        conn.execute("DELETE FROM category_template_version WHERE category_key NOT IN ('razor','curler')")
        fields = ('shop_name', 'stall_no', 'contact_name', 'tg_bot_id', 'tg_bot_username',
                  'owner_tg_username', 'owner_wechat', 'address', 'business_hours',
                  'shipping_info', 'faq')
        conn.execute('UPDATE shop_profile SET ' + ','.join(f'{field}=?' for field in fields)
                     + ",updated_at=datetime('now') WHERE id=1", ('',) * len(fields))
        conn.commit()
    finally:
        conn.close()

    removed = []
    for path in (wechat_binding,):
        if path is None:
            continue
        for candidate in (path, path.with_suffix('.session.json'), path.with_suffix('.lock')):
            if candidate.exists():
                candidate.unlink()
                removed.append(str(candidate))
    if image_dir is not None and image_dir.exists():
        image_dir = image_dir.resolve()
        if image_dir in (Path('/'), Path.home(), database.parent):
            raise SystemExit('图片目录过于宽泛，拒绝清理')
        for child in image_dir.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        removed.append(str(image_dir))
    result = {'database': str(database), 'shop_id': shop_id, 'backup': str(backup),
              'cleared_rows': counts, 'removed_binding_paths': removed}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--shop-id', help='必须与数据库中的 shop_id 一致')
    parser.add_argument('--wechat-binding-file', type=Path,
                        help='明确指定本档口微信绑定文件后才会清理')
    parser.add_argument('--image-dir', type=Path, help='明确指定本档口图片目录后才会清理')
    parser.add_argument('--yes', action='store_true', required=True,
                        help='确认已停止写入该数据库并允许清理')
    args = parser.parse_args()
    reset(args.db, expected_shop_id=args.shop_id,
          wechat_binding=args.wechat_binding_file, image_dir=args.image_dir)
