"""流程用例：开始前问现金科目（A3 / P2-2）。现购单（已付）而账套现金科目不能记账 → 网页提交前弹窗问记到哪个可记账科目
（名称含 เงินสด 的排前）→ 按账套记住 → 后台直写的载荷带 cash_account → 再开现购单不再问。名称含 เงินสด 的可记账科目只有一个 →
不问、自动用它；赊购不问。共用站点脚本见 `book_questions_lib`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_questions_lib as lib  # noqa: E402

TITLE = "开始前问现金科目：现金科目不能记账才问、只一个现金科目自动用、按账套记住"
steps, judge, cleanup = lib.steps, lib.judge, lib.cleanup


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "cash", f"""
supplier = suppliers.create_supplier(supplier_name="ผู้ขายเงินสด " + tag, tax_id={lib.tax_id("611174")!r})["name"]
frappe.db.commit()
rows = [{{"code": "EXP-C-" + tag, "name": "สินค้าเงินสด " + tag, "kind": "stock", "unit": "ถง", "negallow": "", "balance": "1", "std_cost": "0"}}]
two_cash = ACCOUNTS + [{{"code": "11-01-01-01", "name": "เงินสด", "type": "1"}}, {{"code": "11-01-01-02", "name": "เงินสดย่อย", "type": "1"}}]
settings = {{"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "0"}}
book(rows, settings, cash="11-01-00-00", accounts=two_cash)

def form(paid):
    return {{"company": company, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
             "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0, "is_paid": paid,
             "payment_method": "cash", "doc_no": "BQ-C-" + tag, "warehouse": warehouse,
             "lines": [{{"item_type": "service", "description": "ค่าบริการ " + tag, "qty": 1, "unit": "งาน", "unit_price": 200,
                        "vat_rate": 7}}]}}

first = settle("Purchase Document", form(1), api.preflight("Purchase Document", json.dumps(form(1)))["needed"])
out["first"] = mine(first)
cash = next((i for i in first if i["kind"] == "cash_account"), None)
if cash:
    out["after_answer"] = mine(api.preflight("Purchase Document", json.dumps(form(1)),
                                             json.dumps([{{"kind": "cash_account", "subject": cash["subject"], "value": "11-01-01-02"}}]))["needed"])
out["again"] = mine(api.preflight("Purchase Document", json.dumps(form(1)))["needed"])
out["written"] = (background("Purchase Document", form(1)).get("payload") or {{}}).get("cash_account")
out["credit"] = mine(api.preflight("Purchase Document", json.dumps(form(0)))["needed"])
for row in frappe.get_all(choices.CHOICE, filters={{"company": company, "kind": "cash_account", "subject": ["like", key + "|%"]}},
                          pluck="name", limit_page_length=0):
    frappe.delete_doc(choices.CHOICE, row, ignore_permissions=True, force=True)
frappe.db.commit()
book(rows, settings, cash="11-01-00-00", accounts=ACCOUNTS + [{{"code": "11-01-01-01", "name": "เงินสด", "type": "1"}}])
out["one_cash"] = mine(api.preflight("Purchase Document", json.dumps(form(1)))["needed"])
out["one_cash_written"] = (background("Purchase Document", form(1)).get("payload") or {{}}).get("cash_account")
""")


def verify(lab, manifest, params, camera_code=None):
    first = params.get("first") or []
    cash = next((row for row in first if row["kind"] == "cash_account"), {})
    return [
        {"case": "现购而现金科目不能记账：提交前问，名称含 เงินสด 的两个科目排前", "seen": first,
         "ok": cash.get("options", [])[:2] == ["11-01-01-01", "11-01-01-02"]},
        {"case": "选了以后不再问；后台直写的载荷 cash_account 是选的那个", "seen": [params.get("after_answer"), params.get("written")],
         "ok": params.get("after_answer") == [] and params.get("written") == "11-01-01-02"},
        {"case": "第二次现购：按账套记住，不再问", "seen": params.get("again"), "ok": params.get("again") == []},
        {"case": "赊购：不问", "seen": params.get("credit"), "ok": params.get("credit") == []},
        {"case": "只有一个名称含 เงินสด 的可记账科目：不问、自动用它", "seen": [params.get("one_cash"), params.get("one_cash_written")],
         "ok": params.get("one_cash") == [] and params.get("one_cash_written") == "11-01-01-01"},
    ]
