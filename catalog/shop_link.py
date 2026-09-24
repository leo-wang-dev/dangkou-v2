"""One merchant per database: stable identity, verified bot binding, note provenance."""
import json
import uuid

PROFILE_FIELDS = ('shop_id', 'shop_name', 'stall_no', 'contact_name', 'tg_bot_id', 'tg_bot_username')
SUPPLIER_FIELDS = ('档口名称', '档口号/地址', '供应商联系人', '供应商联系方式')
INTERNAL_NOTE_FIELDS = frozenset({'商品编号', '商品类别'})


def migrate(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(shop_profile)')}
    for key in PROFILE_FIELDS:
        if key not in cols:
            conn.execute(f"ALTER TABLE shop_profile ADD COLUMN {key} TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE shop_profile SET shop_id=? WHERE id=1 AND shop_id=''", ('shop_' + uuid.uuid4().hex,))
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS shop_identity ON shop_profile(shop_id)')
    conn.execute("""CREATE TRIGGER IF NOT EXISTS immutable_shop_identity BEFORE UPDATE OF shop_id ON shop_profile
      WHEN NEW.shop_id != OLD.shop_id BEGIN SELECT RAISE(ABORT,'shop identity is immutable'); END""")
    from .templates import TEMPLATES
    for t in TEMPLATES.values():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({t.table})')}
        if 'shop_id' not in cols:
            conn.execute(f'ALTER TABLE {t.table} ADD COLUMN shop_id TEXT REFERENCES shop_profile(shop_id)')
        conn.execute(f'UPDATE {t.table} SET shop_id=(SELECT shop_id FROM shop_profile WHERE id=1) WHERE shop_id IS NULL')
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {t.table}_owner_insert AFTER INSERT ON {t.table}
          BEGIN UPDATE {t.table} SET shop_id=COALESCE(NEW.shop_id,(SELECT shop_id FROM shop_profile WHERE id=1)) WHERE id=NEW.id; END""")
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {t.table}_owner_guard BEFORE UPDATE OF shop_id ON {t.table}
          WHEN NEW.shop_id IS NULL OR NEW.shop_id != (SELECT shop_id FROM shop_profile WHERE id=1)
          BEGIN SELECT RAISE(ABORT,'product belongs to another shop'); END""")
    cols = {r[1] for r in conn.execute('PRAGMA table_info(cs_note)')}
    for key, sql_type in (
        ('received_shop_id', 'TEXT REFERENCES shop_profile(shop_id)'),
        ('source_shop_id', 'TEXT REFERENCES shop_profile(shop_id)'),
        ('catalog_photo', "TEXT NOT NULL DEFAULT ''"),
        ('catalog_fields', "TEXT NOT NULL DEFAULT '{}'"),
        ('source_basis', "TEXT NOT NULL DEFAULT 'unknown'"),
    ):
        if key not in cols:
            conn.execute(f'ALTER TABLE cs_note ADD COLUMN {key} {sql_type}')
    for key in ('received_shop_id', 'source_shop_id'):
        for operation in ('INSERT', 'UPDATE'):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS note_{key}_{operation.lower()} BEFORE {operation} ON cs_note
              WHEN NEW.{key} IS NOT NULL AND NOT EXISTS(SELECT 1 FROM shop_profile WHERE shop_id=NEW.{key})
              BEGIN SELECT RAISE(ABORT,'unknown shop identity'); END""")


def profile(conn):
    return dict(conn.execute('SELECT * FROM shop_profile WHERE id=1').fetchone())


def validate_binding(conn, changes):
    current = profile(conn)
    # Offsets/customer history are scoped to one bot. Rebinding needs an explicit migration.
    if current['tg_bot_id'] and 'tg_bot_id' in changes and changes['tg_bot_id'] != current['tg_bot_id']:
        raise ValueError('已有 bot 绑定不可直接替换；更换 bot 须先迁移收发记录')
    merged = {**current, **changes}
    if merged['tg_bot_id'] and not merged['shop_name']:
        raise ValueError('绑定 bot 前请填写档口名称')


def verify_bot(conn, identity):
    p = profile(conn)
    if not p['shop_name'] or not p['tg_bot_id']:
        raise ValueError('未完成档口名称和 TG bot ID 绑定，请在档口端补充并审批')
    if not isinstance(identity, dict) or not identity.get('is_bot') or str(identity.get('id')) != p['tg_bot_id']:
        raise ValueError('TG Token 所属 bot 与本数据库的档口绑定不一致，拒绝启动')
    return p['shop_id']


def supplier_values(p):
    contacts = []
    if p['owner_tg_username']:
        contacts.append('TG @' + p['owner_tg_username'])
    if p['owner_wechat']:
        contacts.append('微信 ' + p['owner_wechat'])
    return dict(zip(SUPPLIER_FIELDS, (p['shop_name'], ' '.join(x for x in (p['stall_no'],p['address']) if x),
                                     p['contact_name'], '；'.join(contacts))))


def origin(conn, fields):
    p = profile(conn)
    if not p['tg_bot_id'] or not p['shop_name']:
        return None, None, 'unknown'
    # Receiving shop is certain; an external supplier in a photo must not be overwritten.
    has_external = any(fields.get(k) not in (None, '', '待补充', '未拍到', '模糊', p['shop_name'] if k=='档口名称' else None)
                       for k in SUPPLIER_FIELDS)
    return p['shop_id'], None if has_external else p['shop_id'], 'photo' if has_external else 'bot_context'


def fields_for(conn, note):
    from .cs_supplier import normalize
    note = dict(note)
    fields = normalize(json.loads(note['fields_json']))
    if note.get('source_shop_id'):
        p = profile(conn)
        if p['shop_id'] == note['source_shop_id']:
            fields.update({k:v or '待补充' for k,v in supplier_values(p).items()})
    if note.get('customer_id'):
        card = conn.execute("SELECT fields_json FROM cs_card_info WHERE customer_id=?",
                            (note['customer_id'],)).fetchone()
        if card:
            try:
                fields.update({k: v for k, v in json.loads(card['fields_json']).items()
                               if str(v or '').strip() and '未拍到' not in str(v)})
            except (TypeError, ValueError):
                pass
    basis = note.get('source_basis', 'unknown')
    fields['档口归属依据'] = {'bot_context':'接待档口（供货关系待确认）','customer_confirmed':'客户确认本店',
                              'manual':'客户填写','photo':'照片信息（待确认）','unknown':'待确认'}.get(basis,'待确认')
    return fields


def customer_fields(conn, note):
    """Return note fields safe for customer pages, files, and model prompts."""
    fields = fields_for(conn, note)
    for key in INTERNAL_NOTE_FIELDS:
        fields.pop(key, None)
    fields.pop('档口归属依据', None)   # 内部留档口径，客户不可见
    return fields


def snapshot(conn, note):
    result = dict(note)
    result['fields_json'] = json.dumps(customer_fields(conn,note),ensure_ascii=False)
    return result


def set_field(conn, note, field, value):
    note = dict(note)
    if field in ('商品编号','商品类别'):
        raise ValueError('商品关联由系统校验，请选择商品或修改型号或品名')
    if field == '档口归属依据':
        raise ValueError('档口归属依据由系统记录，请修改档口名称或明确指定本店')
    fields = fields_for(conn, note)
    fields.pop('档口归属依据',None)
    source = note.get('source_shop_id')
    basis = note.get('source_basis','unknown')
    if field == '型号或品名' and value != fields.get(field) and fields.get('商品编号'):
        clear_catalog(conn,note)
        note=dict(conn.execute('SELECT * FROM cs_note WHERE id=?',(note['id'],)).fetchone())
        fields=fields_for(conn,note)
        fields.pop('档口归属依据',None)
        source,basis=note['source_shop_id'],note['source_basis']
    if field == '档口名称' and value in ('本店','当前档口'):
        p = profile(conn)
        if not p['shop_name'] or not p['tg_bot_id']:
            raise ValueError('当前档口尚未完成 bot 绑定')
        source, basis = p['shop_id'], 'customer_confirmed'
        fields.update({k:v or '待补充' for k,v in supplier_values(p).items()})
    else:
        if field in SUPPLIER_FIELDS:
            if field == '档口名称' and value != fields.get('档口名称'):
                fields.update({k:'待补充' for k in SUPPLIER_FIELDS})
            source, basis = None, 'manual'
        fields[field] = value
    conn.execute('UPDATE cs_note SET fields_json=?,source_shop_id=?,source_basis=? WHERE id=? AND customer_id=?',
                 (json.dumps(fields,ensure_ascii=False),source,basis,note['id'],note['customer_id']))


def clear_catalog(conn,note):
    """Remove only catalog-derived data; preserve customer photos and edited fields."""
    note=dict(note)
    fields=json.loads(note['fields_json'])
    fields.pop('商品编号',None)
    fields.pop('商品类别',None)
    for key,old in json.loads(note.get('catalog_fields') or '{}').items():
        if fields.get(key)==old:fields.pop(key,None)
    photo=note.get('photo','')
    if photo and photo==note.get('catalog_photo'):photo=''
    basis='bot_context' if note.get('source_shop_id') else 'unknown'
    conn.execute("UPDATE cs_note SET fields_json=?,photo=?,catalog_photo='',catalog_fields='{}',source_basis=? WHERE id=?",
                 (json.dumps(fields,ensure_ascii=False),photo,basis,note['id']))
