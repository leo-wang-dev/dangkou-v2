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
CREATE TABLE IF NOT EXISTS category_template (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  fields_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'approved',
  storage TEXT NOT NULL DEFAULT 'dynamic',
  source_sheet TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS category_template_version (
  category_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  approved_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(category_key, version)
);
CREATE TABLE IF NOT EXISTS product_dynamic (
  id TEXT PRIMARY KEY,
  category_key TEXT NOT NULL,
  inner_code TEXT UNIQUE NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'approved',
  image_main TEXT NOT NULL DEFAULT '',
  images_json TEXT NOT NULL DEFAULT '[]',
  source_doc INTEGER,
  source_key TEXT NOT NULL DEFAULT '',
  source_sheet TEXT NOT NULL DEFAULT '',
  source_row INTEGER,
  row_fingerprint TEXT NOT NULL DEFAULT '',
  cs_visible INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(category_key) REFERENCES category_template(key)
);
CREATE INDEX IF NOT EXISTS idx_dynamic_category ON product_dynamic(category_key, status, cs_visible);
CREATE INDEX IF NOT EXISTS idx_dynamic_source ON product_dynamic(category_key, source_key, source_sheet, source_row);
-- ===== C端客服（平台公共红线优先，所有报价转人工）=====
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
  used INTEGER DEFAULT 0, expires_at TEXT DEFAULT (datetime('now','+2 days')),
  created_at TEXT DEFAULT (datetime('now')));


CREATE TABLE IF NOT EXISTS shop_profile (
    id INTEGER PRIMARY KEY CHECK(id=1),
    owner_tg_username TEXT NOT NULL DEFAULT '',
    owner_wechat TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO shop_profile(id) VALUES(1);
CREATE TABLE IF NOT EXISTS cs_inbox (
    update_id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS cs_outbox (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    recipient TEXT,
    body TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cs_context (
    customer_id TEXT PRIMARY KEY,
    product_id TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cs_photo_candidates (
    customer_id TEXT PRIMARY KEY,
    candidates TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_retry (
    product_id TEXT NOT NULL, category TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT '1970-01-01',
    last_error TEXT,
    PRIMARY KEY(product_id,category)
);
