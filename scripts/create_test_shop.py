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
                ("test_shop/test-rz-8226.jpg", "TEST-RZ-8226", "#345995"),
                ("test_shop/test-curl-2026.jpg", "TEST-CURL-2026", "#9b5de5"),
            ):
                _placeholder(images / filename, title, color)
            # 测试商品走动态分类（与生产同一条链路）
            from catalog import dynamic_catalog
            fields = [
                {"key": "model", "label": "型号", "type": "text", "visibility": "public",
                 "searchable": True, "role": "model", "required": False},
                {"key": "price", "label": "价格", "type": "money", "visibility": "internal",
                 "searchable": False, "role": "price", "required": False},
                {"key": "spec", "label": "规格", "type": "text", "visibility": "public",
                 "searchable": False, "role": "spec", "required": False},
                {"key": "ctn", "label": "箱规", "type": "text", "visibility": "public",
                 "searchable": False, "role": "spec", "required": False},
            ]
            dynamic_catalog.approve_template(conn, {
                "key": "test_cat", "name": "全链路测试品类", "storage": "dynamic",
                "source_sheet": "全链路测试品类", "fields": fields})
            dynamic_catalog.upsert_approved_products(conn, "test_cat", [
                {"id": "test-rz-8226", "inner_code": "TEST-INNER-RZ",
                 "data": {"model": "TEST-RZ-8226", "price": "12.80",
                          "spec": "便携式测试商品 黑色/银色 155×65×45mm",
                          "ctn": "QTY: 40 PCS/CTN"},
                 "images": ["test_shop/test-rz-8226.jpg"], "cs_visible": 1},
                {"id": "test-curl-2026", "inner_code": "TEST-INNER-CURL",
                 "data": {"model": "TEST-CURL-2026", "price": "18.50",
                          "spec": "测试商品 110-240V 44W",
                          "ctn": "QTY: 24 PCS/CTN MEAS:52*39*36cm"},
                 "images": ["test_shop/test-curl-2026.jpg"], "cs_visible": 1},
            ])
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
