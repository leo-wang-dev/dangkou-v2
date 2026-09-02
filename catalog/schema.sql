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
