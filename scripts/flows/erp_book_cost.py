"""流程用例：开始前问出库成本（A3 / P2-2）。账套允许负库存、已存的销售单卖一件结存 0 又没有标准成本的物料 → CC 直写的开始前
确认卡片问「不结转成本 / 填单位成本」（不预选）→ 答了只对这一张单 → 准备好的载荷带 cost_choice → 另一张同样的单照样再问（不记）。
有正结存的物料不问。共用站点脚本见 `book_questions_lib`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_questions_lib as lib  # noqa: E402

TITLE = "开始前问出库成本：无成本基础才问、不预选、只对这一张单、下一张照问"
steps, judge, cleanup = lib.steps, lib.judge, lib.cleanup


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "cost", f"""
from pearnly_erp_portal.sales import entry
customer = customers.create_customer(customer_name="ลูกค้าต้นทุน " + tag, tax_id={lib.tax_id("611173")!r})["name"]
zero = products.create_product(product_name="ของหมด " + tag, unit="ถัง", unit_price=50, default_cost=10)["name"]
full = products.create_product(product_name="ของมี " + tag, unit="ถัง", unit_price=50, default_cost=10)["name"]
for product, code in ((zero, "EXP-Z-" + tag), (full, "EXP-F-" + tag)):
    frappe.db.set_value("ERP Product", product, {{"express_company": company, "express_code": code, "express_stock": 1}})
frappe.db.commit()
book([{{"code": "EXP-Z-" + tag, "name": "ของหมด " + tag, "kind": "stock", "unit": "ถง", "negallow": "", "balance": "0", "std_cost": "0"}},
      {{"code": "EXP-F-" + tag, "name": "ของมี " + tag, "kind": "stock", "unit": "ถง", "negallow": "", "balance": "9", "std_cost": "0"}}],
     {{"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "0"}})

def sale(product, n):
    doc = entry.post({{"company": company, "customer": customer, "doc_date": frappe.utils.nowdate(), "has_vat": 1, "vat_rate": 7,
                      "source": "manual", "doc_kind": "receipt", "is_paid": 0, "paid_amount": 0, "warehouse": warehouse,
                      "lines": [{{"product": product, "item_type": "goods", "description": frappe.db.get_value("ERP Product", product, "product_name"),
                                 "qty": 1, "unit": "ถัง", "unit_price": 50, "vat_applicable": 1, "warehouse": warehouse, "location": ""}}]}},
                     request_id=f"flow-bq-cost-{{n}}-" + tag)
    frappe.db.commit()
    out["docs"].append(["ERP Sales Document", doc["name"]])
    return doc["name"]

one, two, has = sale(zero, 1), sale(zero, 2), sale(full, 3)
found = precheck.collect_document(push.SALES, one)
out["first"] = mine(found["items"])
cost = next((i for i in found["items"] if i["kind"] == "cost_basis"), None)
if cost:
    precheck.save(company, found, [{{"kind": "cost_basis", "subject": cost["subject"], "value": "given:7.5"}}])
    frappe.db.commit()
ready = precheck.collect_document(push.SALES, one)
out["after_answer"] = mine(ready["items"])
out["written"] = ((ready["result"].get("payload") or {{}}).get("items") or [{{}}])[0]
out["other_doc"] = mine(precheck.collect_document(push.SALES, two)["items"])
out["with_stock"] = mine(precheck.collect_document(push.SALES, has)["items"])
""")


def verify(lab, manifest, params, camera_code=None):
    first = params.get("first") or []
    cost = next((row for row in first if row["kind"] == "cost_basis"), {})
    written = params.get("written") or {}
    return [
        {"case": "结存 0、没有标准成本：确认卡片问「不结转 / 填单位成本」，不预选，「填单位成本」带数字输入", "seen": first,
         "ok": cost.get("options") == ["none", "given"] and cost.get("entry") == ["given"] and not cost.get("value")},
        {"case": "答「填单位成本 7.5」后这张不再问；载荷带 cost_choice given、cost 7.5", "seen": [params.get("after_answer"), written],
         "ok": params.get("after_answer") == [] and (written.get("cost_choice"), written.get("cost")) == ("given", "7.5")},
        {"case": "另一张同样的单：照样再问（不跨单记）", "seen": params.get("other_doc"),
         "ok": any(row["kind"] == "cost_basis" for row in params.get("other_doc") or [])},
        {"case": "有正结存的物料：不问", "seen": params.get("with_stock"), "ok": params.get("with_stock") == []},
    ]
