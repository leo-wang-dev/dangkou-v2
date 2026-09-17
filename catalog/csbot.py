"""C端 TG 客服机器人脑：拍照整理 + persona 询价 + 红线转人工。

分层：transport(TgApi) / llm / 本模块=流程编排。测试注入假件，不打网络。
"""
import json
import os
import secrets

from . import cs

TRANSFER_MARK = '<<TRANSFER>>'
HOLD_THE_LINE = '您好，这个情况我需要请商家来回复您，商家马上来～'

EXTRACT_PROMPT = (
    '你是档口商品信息抽取器。这是采购员在批发市场随手拍的照片。'
    '对照片里每个可辨认的商品抽取字段：型号或品名、价格、装箱数、颜色、体积或尺寸、'
    '起订量、其他值得记录的信息（名片信息放"其他"）。'
    '照片里没有的字段明确标"未拍到"，看不清标"模糊"。如实抽取，禁止编造数字。'
    '只输出 JSON 数组，不要输出其他文字。')

PERSONA_BASE = (
    '你是义乌档口的智能客服，替商家接待采购员。规则：\n'
    '1) 只报商品库中"可观测=开"的商品，按阶梯价回答（¥，人民币），价格数字只用系统提供的，自己不做算术不浮动；\n'
    '2) 商品库没有的货不编造，回复请商家确认；\n'
    '3) 正式报价单/盖章文件不代做，请客户等商家；\n'
    '4) 对照下面的红线知识判断每条消息：命中任何一条 → 只回复 '
    f'{TRANSFER_MARK}加上你引用的那条红线原文，不要回答业务内容；\n'
    '5) 未命中正常接待，简短友好，中文。\n\n')


class CsBot:
    def __init__(self, conn, api, llm=None, notifier=None, img_dir=None):
        self.conn = conn
        self.api = api
        from . import llm as _llm
        self.llm = llm or _llm
        self.notifier = notifier or self._wechat_remind
        self.img_dir = img_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'cs_photos')
        os.makedirs(self.img_dir, exist_ok=True)

    # ---------- 主入口 ----------

    def handle_update(self, upd: dict):
        msg = upd.get('message')
        if not msg:
            return
        chat_id = msg['chat']['id']
        cust = self._ensure_customer(msg.get('from', {}))
        text = msg.get('text')
        if msg.get('photo'):
            self._reply(chat_id, self._on_photo(cust, msg))
        elif msg.get('voice') is not None:
            self._reply(chat_id, '收到语音啦，我还听不懂语音，麻烦您打字或拍照告诉我～')
        elif text:
            self._log(cust['id'], 'user', text)
            self._reply(chat_id, self._on_text(cust, text))

    # ---------- 拍照整理 ----------

    def _on_photo(self, cust, msg) -> str:
        data = self.api.download_photo(msg['photo'])
        fname = f"{cust['id']}_{secrets.token_hex(6)}.jpg"
        path = os.path.join(self.img_dir, fname)
        open(path, 'wb').write(data)
        raw = self.llm.chat_vision(EXTRACT_PROMPT, data)
        items = self._parse_items(raw)
        if not items:
            self._log(cust['id'], 'assistant', '(抽取失败)')
            return '这张照片我没能认出商品信息，麻烦重拍一张近一点的～'
        receipts = []
        for i, fields in enumerate(items, 1):
            self.conn.execute(
                'INSERT INTO cs_note(customer_id, photo, fields_json, status) '
                "VALUES(?,?,?,'draft')",
                (cust['id'], path, json.dumps(fields, ensure_ascii=False)))
            got = [f'{k}={v}' for k, v in fields.items()
                   if v and '未拍到' not in str(v) and '模糊' not in str(v)]
            miss = [k for k, v in fields.items() if '未拍到' in str(v) or '模糊' in str(v)]
            line = f'【{i}】' + '；'.join(got)
            if miss:
                line += f'\n　没拍到/看不清：{"、".join(miss)}（可以回复我补上，比如"颜色黑色"）'
            receipts.append(line)
        self._log(cust['id'], 'assistant', '\n'.join(receipts))
        return ('整理好了，请核对：\n' + '\n'.join(receipts)
                + '\n\n回复"确认"入清单；要改就直接说，比如"1 价格改成2.5"。说"出表"可随时导出Excel。')

    def _parse_items(self, raw: str) -> list:
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1].lstrip('json').strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [d for d in data if isinstance(d, dict) and d] if isinstance(data, list) \
            else ([data] if isinstance(data, dict) else [])

    # ---------- 文本 / persona ----------

    def _on_text(self, cust, text) -> str:
        low = text.strip()
        if low in ('确认', '确认入库', '确认清单', 'OK', 'ok'):
            return self._confirm_drafts(cust)
        if '出表' in low or '导出' in low:
            return self._make_link(cust)
        if '确认' in low and any(c.isdigit() for c in low):   # "确认"之外的修正语句走 persona
            pass
        system = PERSONA_BASE + cs.build_knowledge(self.conn) + self._catalog_brief()
        history = self._history(cust['id'])
        reply = self.llm.chat_text(system, history + [{'role': 'user', 'content': text}])
        if TRANSFER_MARK in reply:
            cited = reply.split(TRANSFER_MARK, 1)[1].strip() or '（未引用红线原文）'
            raw = cs.get_redline(self.conn)['text_raw']
            cite_part = cited if cited == raw else f'{cited}（原文：{raw}）'
            self.notifier(
                f'🔔 转人工提醒\n客户：{cust["tg_name"] or cust["tg_id"]}\n'
                f'原话：{text}\n命中红线：{cite_part}\n请在 Telegram 里回复客户。')
            self._log(cust['id'], 'assistant', f'[转人工] 命中红线：{cited}')
            return HOLD_THE_LINE
        self._log(cust['id'], 'assistant', reply)
        return reply

    def _catalog_brief(self) -> str:
        """可观测商品+阶梯价简表（persona 报价数据源，代码算好喂给它）。"""
        lines = []
        from .templates import TEMPLATES
        for t in TEMPLATES.values():
            rows = self.conn.execute(
                f"SELECT * FROM {t.table} WHERE cs_visible=1 AND status='approved'").fetchall()
            for r in rows:
                name = r['model_no'] or r['item_no'] or r['inner_code']
                tiers = cs.parse_tiers(r['tier_price'])
                tp = ';'.join(f'{q}个¥{p}' for q, p in tiers) or '未设阶梯价'
                lines.append(f'- {name}（{t.name}）：{tp}')
        return ('\n\n当前可报商品（只能报这些）：\n' + '\n'.join(lines)) if lines else \
            '\n\n当前没有可观测商品，任何询价都请客户等商家。'

    # ---------- 确认 / 出表 ----------

    def _confirm_drafts(self, cust) -> str:
        cur = self.conn.execute(
            "UPDATE cs_note SET status='confirmed' WHERE customer_id=? AND status='draft'",
            (cust['id'],))
        self.conn.commit()
        n = cur.rowcount
        if not n:
            return '目前没有待确认的条目，先拍照发我吧～'
        self._log(cust['id'], 'assistant', f'[确认] {n}条入清单')
        return f'好的，{n} 条已进您的清单。继续拍照，或说"出表"导出Excel。'

    def _make_link(self, cust) -> str:
        token = secrets.token_urlsafe(16)
        self.conn.execute(
            'INSERT INTO cs_link(token, customer_id, used, expires_at) '
            "VALUES(?,?,0,datetime('now','+2 days'))", (token, cust['id']))
        self.conn.commit()
        base = os.environ.get('CATALOG_V2_PUBLIC_URL', 'http://127.0.0.1:8890')
        url = f'{base}/cs/list.html?k={token}'
        self._log(cust['id'], 'assistant', f'[出表] {url}')
        return f'清单链接（2天内有效）：\n{url}\n打开即可查看和导出Excel。'

    # ---------- 基础设施 ----------

    def _ensure_customer(self, frm: dict) -> dict:
        tg_id = str(frm.get('id', ''))
        row = self.conn.execute('SELECT * FROM cs_customer WHERE tg_id=?', (tg_id,)).fetchone()
        if not row:
            cid = secrets.token_hex(6)
            self.conn.execute(
                'INSERT INTO cs_customer(id, tg_id, tg_name) VALUES(?,?,?)',
                (cid, tg_id, frm.get('username') or frm.get('first_name') or ''))
            self.conn.commit()
            row = self.conn.execute('SELECT * FROM cs_customer WHERE tg_id=?', (tg_id,)).fetchone()
        else:
            self.conn.execute("UPDATE cs_customer SET last_seen=datetime('now') WHERE id=?",
                              (row['id'],))
            self.conn.commit()
        return row

    def _history(self, cust_id, n=6) -> list:
        rows = self.conn.execute(
            'SELECT role, content FROM cs_conversation_log WHERE customer_id=? '
            'ORDER BY id DESC LIMIT ?', (cust_id, n)).fetchall()
        return [{'role': r['role'], 'content': r['content']} for r in reversed(rows)]

    def _log(self, cust_id, role, content):
        self.conn.execute(
            'INSERT INTO cs_conversation_log(customer_id, role, content) VALUES(?,?,?)',
            (cust_id, role, content))
        self.conn.commit()

    def _reply(self, chat_id, text):
        self.api.send_message(chat_id, text)

    @staticmethod
    def _wechat_remind(text):
        """生产：微信推送（复用 catalog-notify 直连发送器）。本地未配 token 时落盘。"""
        url = os.environ.get('CATALOG_NOTIFY_URL', 'http://127.0.0.1:17606/notify')
        token = os.environ.get('CATALOG_NOTIFY_TOKEN', '')
        try:
            import requests
            requests.post(url, json={'text': text}, timeout=15,
                          headers={'Authorization': f'Bearer {token}'})
        except Exception as e:  # noqa: BLE001
            print(f'[cs-remind] 微信提醒发送失败({e})，落盘：{text[:80]}', flush=True)
