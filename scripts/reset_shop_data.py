#!/usr/bin/env python3
"""档口数据清理脚本——演示后/交付老板前的一键重置。

用法（服务器上，需 sudo）：
  sudo python3 scripts/reset_shop_data.py             # 基础：清商品/动态分类/导入与工单/向量索引/cs对话数据
  sudo python3 scripts/reset_shop_data.py --dry-run   # 只打印会清什么，不动手
  sudo python3 scripts/reset_shop_data.py --memory     # 基础 + 清空微信聊天会话记忆（归档备份+重启引擎）
  sudo python3 scripts/reset_shop_data.py --handover   # memory + 档口资料/红线/TG客户Bot状态（换老板全套重置）

永远保留（任何级别都不动）：
  微信扫码绑定文件与 im 账号、服务令牌(.env)、TG bot 凭据文件 customer-runtime/credentials.json
  （换老板的微信时：让老板在管理页自己重新扫码绑定，那条链路自带旧账号清理）
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import time

DB = os.environ.get('RESET_DB', '/home/ubuntu/dangkou-wechat-test/data/catalog.db')
ENGINE_STATE = os.environ.get('RESET_ENGINE_STATE', '/home/ubuntu/dsh-wechat-test/state')
ENGINE_SERVICE = os.environ.get('RESET_ENGINE_SERVICE', 'dangkou-wechat-test-engine')

BASE_TABLES = ['product_dynamic', 'product_razor', 'product_curler', 'import_doc',
               'embedding', 'cs_note', 'cs_conversation_log', 'cs_customer', 'cs_link']
TICKET_TYPES_BASE = "('import','template_import','product_import','mutate')"


def counts(c, tables):
    out = {}
    for t in tables:
        try:
            out[t] = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        except sqlite3.OperationalError:
            out[t] = '-'
    return out


def wipe_db(dry):
    c = sqlite3.connect(DB)
    c.execute('PRAGMA busy_timeout=8000')
    tables = BASE_TABLES + ['approval_ticket', 'category_template']
    print('清前:', counts(c, tables))
    if dry:
        c.close()
        return
    dyn = [r[0] for r in c.execute(
        "SELECT key FROM category_template WHERE storage='dynamic'").fetchall()]
    for t in BASE_TABLES:
        try:
            c.execute(f'DELETE FROM {t}')
        except sqlite3.OperationalError:
            pass
    c.execute(f"DELETE FROM approval_ticket WHERE ticket_type IN {TICKET_TYPES_BASE}")
    c.execute("DELETE FROM category_template WHERE storage='dynamic'")
    if dyn:
        c.execute(f"DELETE FROM category_template_version WHERE category_key IN "
                  f"({','.join('?' * len(dyn))})", dyn)
    c.commit()
    print('清后:', counts(c, tables))
    c.close()


def wipe_sessions(dry):
    src = os.path.join(ENGINE_STATE, 'sessions')
    if not os.path.isdir(src):
        print(f'会话目录不存在: {src}')
        return
    # 引擎持久化插件把会话放在“工作区路径净化名”子目录下（形如
    # --home-...-workspace--/<session-id>/session.jsonl），只按顶层 wechat-*
    # 扫会漏（曾因此漏清当天测试记忆）。凡名字不含 backup 的都算活数据。
    live = [e for e in os.listdir(src) if 'backup' not in e.lower()]
    n_sessions = 0
    for e in live:
        for _root, _dirs, files in os.walk(os.path.join(src, e)):
            n_sessions += sum(1 for f in files if f == 'session.jsonl')
    print(f'会话目录: {len(live)} 个活目录 / {n_sessions} 个聊天会话'
          + ('（dry-run 不动）' if dry else ''))
    if dry or not live:
        return
    dst = os.path.join(ENGINE_STATE, f'sessions-backup-{time.strftime("%Y%m%d-%H%M%S")}')
    os.makedirs(dst)
    for e in live:
        shutil.move(os.path.join(src, e), os.path.join(dst, e))
    r = subprocess.run(['systemctl', 'restart', ENGINE_SERVICE])
    time.sleep(4)
    status = subprocess.run(['systemctl', 'is-active', ENGINE_SERVICE],
                            capture_output=True, text=True).stdout.strip()
    print(f'会话已归档到 {dst}；引擎重启: {status}（退出码 {r.returncode}）')


def wipe_handover(dry):
    c = sqlite3.connect(DB)
    c.execute('PRAGMA busy_timeout=8000')
    keep = {'id', 'shop_id', 'updated_at'}
    try:
        cols = [r[1] for r in c.execute('PRAGMA table_info(shop_profile)')]
        sets = ', '.join(f"{col}=''" for col in cols if col not in keep)
        if sets:
            print('档口资料清空' + ('（dry-run）' if dry else ''))
            if not dry:
                c.execute(f'UPDATE shop_profile SET {sets}')
        for t in ('cs_redline',):
            try:
                n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                print(f'{t}: {n} 行' + ('（dry-run）' if dry else ' → 清'))
                if not dry:
                    c.execute(f'DELETE FROM {t}')
            except sqlite3.OperationalError:
                pass
        try:
            c.execute('DELETE FROM approval_ticket')  # 换人：历史工单全清
        except sqlite3.OperationalError:
            pass
        if not dry:
            c.commit()
    finally:
        c.close()
    runtime = os.path.join(os.path.dirname(DB), 'customer-runtime')
    if os.path.isdir(runtime):
        for f in ('status.json',):
            p = os.path.join(runtime, f)
            if os.path.exists(p):
                print(f'清理 {f}' + ('（dry-run）' if dry else ''))
                if not dry:
                    os.remove(p)
    print('TG 客户Bot 将回到未绑定状态（credentials.json 保留，如需换号让老板在管理页重绑）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--memory', action='store_true', help='同时清聊天会话记忆')
    ap.add_argument('--handover', action='store_true', help='交付换人级：memory+档口资料+红线+TG状态')
    args = ap.parse_args()
    print(f'目标库: {DB}')
    wipe_db(args.dry_run)
    if args.handover or args.memory:
        wipe_sessions(args.dry_run)
    if args.handover:
        wipe_handover(args.dry_run)
    if not args.dry_run:
        c = sqlite3.connect(DB)
        print('终态:', counts(c, BASE_TABLES + ['approval_ticket', 'category_template']))
        c.close()
    print('完成。保留：微信绑定/服务令牌/TG凭据文件')


if __name__ == '__main__':
    main()
