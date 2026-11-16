"""流程用例：开始前问新物料的负库存（A3 / P2-2）。账套不许负库存、销售单上有要随单新建的物料（结存 0）→ 网页提交前弹窗问
「设为允许负库存」（弹窗里关掉就是不提交，不给「先留下」）→ 选了按账套记住 → 后台直写的载荷那一行 negallow = Y → 同一本账套
再卖别的新物料不再问。账套允许负库存 → 不问。共用站点脚本见 `book_questions_lib`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_questions_lib as lib  # noqa: E402

TITLE = "开始前问新物料负库存：不许负库存的账套才问、选允许按账套记住、载荷带 negallow"
steps, judge, cleanup = lib.steps, lib.judge, lib.cleanup


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "negative", f"""
customer = customers.create_customer(customer_name="ลูกค้าติดลบ " + tag, tax_id={lib.tax_id("611172")!r})["name"]
items = [products.create_product(product_name=f"ของใหม่ {{n}} " + tag, unit="ถัง", unit_price=50, default_cost=10)["name"]
         for n in (1, 2)]
frappe.db.commit()
book([{{"code": "OTHER-" + tag, "name": "สินค้าอื่น " + tag, "kind": "stock", "unit": "ถง", "negallow": "", "balance": "3",
       "std_cost": "0"}}], {{"mainloc": "01", "negallow": "N", "negauth": "", "chkneglev": "0"}})

def form(product):
    return {{"company": company, "customer": customer, "doc_date": frappe.utils.nowdate(), "has_vat": 1, "vat_rate": 7,
             "source": "manual", "doc_kind": "receipt", "is_paid": 0, "paid_amount": 0, "warehouse": warehouse,
             "lines": [{{"product": product, "item_type": "goods", "description": frappe.db.get_value("ERP Product", product, "product_name"),
                        "qty": 2, "unit": "ถัง", "unit_price": 50, "vat_applicable": 1, "warehouse": warehouse, "location": ""}}]}}

def answer_items(found):
    # 物料题（新建 / 对应已有）照弹窗里选「新建」
    given = [{{"kind": i["kind"], "subject": i["subject"], "value": "__new__"}} for i in found if i["kind"] == "item"]
    out.setdefault("item_subjects", []).extend(i["subject"] for i in found if i["kind"] == "item")
    return given

first = api.preflight("ERP Sales Document", json.dumps(form(items[0])))["needed"]
picked = answer_items(first)
first = api.preflight("ERP Sales Document", json.dumps(form(items[0])), json.dumps(picked))["needed"] if picked else first
first = settle("ERP Sales Document", form(items[0]), first)
out["first"] = mine(first)
negative = next((i for i in first if i["kind"] == "negative_stock"), None)
if negative:
    out["after_answer"] = mine(api.preflight("ERP Sales Document", json.dumps(form(items[0])),
                                             json.dumps([{{"kind": "negative_stock", "subject": negative["subject"], "value": "Y"}}]))["needed"])
out["written"] = background("ERP Sales Document", form(items[0])).get("payload", {{}}).get("items", [{{}}])[0]
second = api.preflight("ERP Sales Document", json.dumps(form(items[1])))["needed"]
picked = answer_items(second)
second = api.preflight("ERP Sales Document", json.dumps(form(items[1])), json.dumps(picked))["needed"] if picked else second
out["second_item"] = mine(second)
book([{{"code": "OTHER-" + tag, "name": "สินค้าอื่น " + tag, "kind": "stock", "unit": "ถง", "negallow": "", "balance": "3",
       "std_cost": "0"}}], {{"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "0"}})
out["book_allows"] = mine(api.preflight("ERP Sales Document", json.dumps(form(items[0])))["needed"], ("negative_stock",))
""")


def verify(lab, manifest, params, camera_code=None):
    first = params.get("first") or []
    negative = next((row for row in first if row["kind"] == "negative_stock"), {})
    return [
        {"case": "不许负库存的账套卖新物料：提交前问，只给「允许负库存」、不预选（弹窗关掉就是不提交）", "seen": first,
         "ok": negative.get("options") == ["Y"] and not negative.get("value")},
        {"case": "选允许后不再问负库存（网页弹窗不问成本）；后台直写的那一行 negallow = Y", "seen": [params.get("after_answer"), params.get("written")],
         "ok": params.get("after_answer") == [] and (params.get("written") or {}).get("negallow") == "Y"},
        {"case": "同一本账套再卖别的新物料：按账套记住，不再问", "seen": params.get("second_item"),
         "ok": not any(row["kind"] == "negative_stock" for row in params.get("second_item") or [])},
        {"case": "账套允许负库存：不问", "seen": params.get("book_allows"), "ok": params.get("book_allows") == []},
    ]
