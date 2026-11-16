"""流程用例：开始前问单位（A3 / P2-2）。票面单位不是这件物料在 Express 的任何单位、ERP 也没给换算率 → 网页提交前弹窗问一次
（选物料的单位，或「填换算」再填一个数）→ 记在这份账套 × 物料 × 票面单位名下 → 后台直写的载荷带上换算率 → 再开同样的单不再问。
票面单位就是物料的单位 → 不问。共用站点脚本见 `book_questions_lib`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_questions_lib as lib  # noqa: E402

TITLE = "开始前问单位：对不上物料单位才问、填换算照写进载荷、第二次不问"
steps, judge, cleanup = lib.steps, lib.judge, lib.cleanup


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "unit", f"""
supplier = suppliers.create_supplier(supplier_name="ผู้ขายหน่วย " + tag, tax_id={lib.tax_id("611171")!r})["name"]
product = products.create_product(product_name="น้ำแข็งแพ็ค " + tag, unit="แพ็ค", unit_price=100, default_cost=100)["name"]
frappe.db.set_value("ERP Product", product, {{"express_company": company, "express_code": "EXP-U-" + tag, "express_stock": 1}})
frappe.db.commit()
book([{{"code": "EXP-U-" + tag, "name": "น้ำแข็งแพ็ค " + tag, "kind": "stock", "unit": "ถง", "purchase_unit": "ลง",
       "purchase_factor": "12", "sale_unit": "", "sale_factor": "0", "negallow": "", "balance": "0", "std_cost": "0"}}],
     {{"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "0"}})

def form(unit):
    return {{"company": company, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
             "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1, "is_paid": 0,
             "doc_no": "BQ-U-" + tag, "warehouse": warehouse,
             "lines": [{{"product": product, "item_type": "goods", "description": "น้ำแข็งแพ็ค " + tag, "qty": 2, "unit": unit,
                        "unit_price": 100, "vat_rate": 7, "warehouse": warehouse, "location": ""}}]}}

first = settle("Purchase Document", form("แพ็ค"), api.preflight("Purchase Document", json.dumps(form("แพ็ค")))["needed"])
out["first"] = mine(first)
out["before"] = background("Purchase Document", form("แพ็ค")).get("payload", {{}}).get("items", [{{}}])[0]
unit = next((i for i in first if i["kind"] == "unit"), None)
if unit:
    out["after_answer"] = mine(api.preflight("Purchase Document", json.dumps(form("แพ็ค")),
                                             json.dumps([{{"kind": "unit", "subject": unit["subject"], "value": "factor:6"}}]))["needed"])
out["again"] = mine(api.preflight("Purchase Document", json.dumps(form("แพ็ค")))["needed"])
out["written"] = background("Purchase Document", form("แพ็ค")).get("payload", {{}}).get("items", [{{}}])[0]
out["own_unit"] = mine(api.preflight("Purchase Document", json.dumps(form("ลัง")))["needed"])
""")


def verify(lab, manifest, params, camera_code=None):
    first = params.get("first") or []
    unit = next((row for row in first if row["kind"] == "unit"), {})
    return [
        {"case": "票面单位对不上：提交前问单位，选项是物料的单位与「填换算」（带数字输入），按采购单位预选", "seen": first,
         "ok": sorted(unit.get("options", [])) == sorted(["ถง", "ลง", "factor"]) and unit.get("entry") == ["factor"]
         and unit.get("value") == "ลง"},
        {"case": "没答前后台直写不新增停下、照旧不带单位键", "seen": params.get("before"),
         "ok": bool(params.get("before")) and "unit_code" not in params["before"] and "factor" not in params["before"]},
        {"case": "答「填换算 6」后不再问；后台直写的载荷带 factor 6", "seen": [params.get("after_answer"), params.get("written")],
         "ok": params.get("after_answer") == [] and (params.get("written") or {}).get("factor") == "6"},
        {"case": "第二次同物料同票面单位：不问", "seen": params.get("again"), "ok": params.get("again") == []},
        {"case": "票面单位就是物料的单位（ลัง = 采购单位）：不问", "seen": params.get("own_unit"), "ok": params.get("own_unit") == []},
    ]
