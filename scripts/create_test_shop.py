"""Create one isolated, clearly labelled test shop for an authenticated TG owner."""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import db, merchant_onboarding as hub, merchant_policy
from catalog.merchant_binding import credentials, root, write_secret


SHOP_NAME = "全链路测试档口（非真实交易）"


def _placeholder(path: Path, title: str, color: str):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (900, 900), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 90, 810, 810), outline="white", width=12)
    draw.text((145, 360), "TEST ONLY", fill="white", stroke_width=2, stroke_fill="black")
    draw.text((145, 450), title, fill="white", stroke_width=2, stroke_fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90)


def create(owner: str):
    c = hub.connect()
    try:
        existing = c.execute(
            """SELECT m.* FROM merchant_access a JOIN merchant m ON m.id=a.merchant_id
               WHERE a.tg_owner=? AND a.role='test_owner'""", (owner,)).fetchone()
        if existing:
            hub.grant_access(c, owner, existing["id"], role="test_owner", select=True)
            c.commit()
            return dict(existing), False

        mid = secrets.token_hex(12)
        directory = root() / mid
        database = directory / "catalog.db"
        images = directory / "images"
        values = {
            "shop_name": SHOP_NAME,
            "owner_tg_username": "hehhh553",
            "owner_wechat": "TEST_ONLY_WECHAT",
            "quote_rules": "测试规则：仅展示商家原文，不计算成交价；测试数据不可下单或付款。",
            "price": "询问最低价、底价、批量优惠或继续议价时转人工。",
            "payment": "要求月结超过30天、赊账或特殊付款方式时转人工。",
            "custom": "开模、改尺寸、换品牌包装时转人工。",
            "logistics": "要求加急、拼柜、货代、海外代发时转人工。",
            "after_sales": "破损、次品赔付、退货和质保争议时转人工。",
            "other": "违法商品、虚假报关等请求直接拒绝并转人工。",
        }
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = db.connect(str(database))
        try:
            db.init_db(conn)
            conn.execute(
                "UPDATE shop_profile SET shop_name=?,owner_tg_username=?,owner_wechat=? WHERE id=1",
                (SHOP_NAME, values["owner_tg_username"], values["owner_wechat"]),
            )
            for filename, title, color in (
                ("razor/test-rz-8226.jpg", "TEST-RZ-8226", "#345995"),
                ("curler/test-curl-2026.jpg", "TEST-CURL-2026", "#9b5de5"),
            ):
                _placeholder(images / filename, title, color)
            conn.execute(
                """INSERT OR REPLACE INTO product_razor
                (id,inner_code,model_no,description,color,size_mm,giftbox_mm,unit_weight_g,ctn_spec,price,remark,status,image_main,images,cs_visible)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                ("test-rz-8226", "TEST-INNER-RZ", "TEST-RZ-8226", "便携式剃须刀测试商品",
                 "黑色/银色", "155×65×45mm", "170×80×60mm", "210g", "40 PCS/CTN",
                 "12.80", "测试数据，不构成报价", "approved", "razor/test-rz-8226.jpg",
                 json.dumps(["razor/test-rz-8226.jpg"])),
            )
            conn.execute(
                """INSERT OR REPLACE INTO product_curler
                (id,inner_code,item_no,ctn_size,ctn_qty,price,voltage,power,heater,material,frequency,remark,status,image_main,images,cs_visible)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                ("test-curl-2026", "TEST-INNER-CURL", "TEST-CURL-2026", "52×39×36cm", "24 PCS",
                 "18.50", "110-240V", "44W", "PTC", "ABS+陶瓷", "50/60Hz",
                 "测试数据，不构成报价", "approved", "curler/test-curl-2026.jpg",
                 json.dumps(["curler/test-curl-2026.jpg"])),
            )
            merchant_policy.apply(conn, values, 1)
            conn.commit()
        finally:
            conn.close()

        internal_owner = f"{owner}#test#{mid[:8]}"
        c.execute(
            """INSERT INTO merchant(id,owner,state,step,draft,confirmed,revision,db_path,error)
               VALUES(?,?,'catalog_ready',?,?,?,?,?,'')""",
            (mid, internal_owner, len(hub.STEPS), json.dumps(values, ensure_ascii=False),
             json.dumps(values, ensure_ascii=False), 1, str(database)),
        )
        hub.grant_access(c, owner, mid, role="test_owner", select=True)
        write_secret(credentials(mid), {"api_token": secrets.token_urlsafe(32)})
        c.commit()
        return dict(c.execute("SELECT * FROM merchant WHERE id=?", (mid,)).fetchone()), True
    finally:
        c.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Authenticated Telegram numeric user id")
    args = parser.parse_args()
    merchant, created = create(args.owner)
    print(json.dumps({"id": merchant["id"], "created": created,
                      "db_path": merchant["db_path"], "state": merchant["state"]}, ensure_ascii=False))
