CREATE TABLE IF NOT EXISTS product_razor (
  id TEXT PRIMARY KEY, inner_code TEXT UNIQUE NOT NULL,
  model_no TEXT, description TEXT, color TEXT, size_mm TEXT,
  giftbox_mm TEXT, unit_weight_g TEXT, ctn_spec TEXT, price TEXT,
  remark TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'approved',
  image_main TEXT, images TEXT DEFAULT '[]',
  source_doc INTEGER, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_razor_model ON product_razor(model_no);
CREATE TABLE IF NOT EXISTS product_curler (
  id TEXT PRIMARY KEY, inner_code TEXT UNIQUE NOT NULL,
  item_no TEXT, ctn_size TEXT, ctn_qty TEXT, price TEXT, voltage TEXT,
  power TEXT, heater TEXT, material TEXT, frequency TEXT,
  remark TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'approved',
  image_main TEXT, images TEXT DEFAULT '[]',
  source_doc INTEGER, created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_curler_item ON product_curler(item_no);
CREATE TABLE IF NOT EXISTS embedding (
  product_id TEXT NOT NULL, category TEXT NOT NULL,
  image_path TEXT NOT NULL, vec BLOB NOT NULL,
  text_vec BLOB,
  PRIMARY KEY (product_id, image_path)
);
CREATE TABLE IF NOT EXISTS approval_ticket (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_type TEXT NOT NULL,
  category TEXT,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  token TEXT UNIQUE NOT NULL, token_used_at TEXT,
  created_at TEXT DEFAULT (datetime('now')), decided_at TEXT
);
CREATE TABLE IF NOT EXISTS import_doc (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL, category TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'parsing',
  stats_json TEXT, error TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
-- ===== C端客服（v1.2：阶梯价结构化 + 红线自然语言知识）=====
CREATE TABLE IF NOT EXISTS cs_redline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id TEXT NOT NULL DEFAULT '',  -- ''=店级（SQLite UNIQUE 不去重 NULL）；商品级覆盖店级
  text_raw TEXT NOT NULL,        -- 商家原文（留档/审计/审批卡展示）
  text_summary TEXT NOT NULL,    -- 注入 prompt 的总结版（原文冗长时压缩，否则=原文）
  updated_at TEXT DEFAULT (datetime('now')), updated_by TEXT,
  UNIQUE(product_id));
CREATE TABLE IF NOT EXISTS cs_customer (   -- 采购员（=TG账号）
  id TEXT PRIMARY KEY, tg_id TEXT UNIQUE, tg_name TEXT,
  first_seen TEXT DEFAULT (datetime('now')), last_seen TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS cs_note (       -- 清单条目（draft=待确认 confirmed=已入清单）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT NOT NULL, photo TEXT,
  fields_json TEXT NOT NULL,
  status TEXT DEFAULT 'draft', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS cs_conversation_log (  -- 对话留档（运营排查）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT, role TEXT, content TEXT, kind TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS cs_link (       -- 清单临时链接（一次性 token）
  token TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
  used INTEGER DEFAULT 0, expires_at TEXT,
  created_at TEXT DEFAULT (datetime('now')));
