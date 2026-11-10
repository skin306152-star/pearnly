"""流程用例：收银台第一次用 —— 查码、开班、卖一件（10-30：接 Express 的公司同步出多个仓，默认收银台不带仓，第一次收银就报没有仓）。

新开户（接 Express、7 个仓）：先把这家公司现有的收银台清掉（只在实验室，模拟新客户第一次用收银），主仓入库 3 件。打开收银页不能报
「收银台没有仓」；页面上查码认出商品、带出主仓结存；开班建出默认收银台，它挂的是主仓（最先建的启用仓）；卖 1 件现金成交。
老数据（建公司时配了收银台和仓）：同样查码、开班、卖 1 件。库里：收银台挂的是主仓 / 原配的仓、小票已成交、那个仓少 1 件。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 收银台：第一次用就能查码、开班、卖货"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    first = manifest["dataset"] == "new"
    product = manifest["products"]["by_barcode"]["product"] if first else manifest["products"][0]["name"]
    # 这一格开着收银；格结束时 run_flow 还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", company, {"pos_enabled": 1})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents, warehouses
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
if {first!r}:
    # 模拟新客户第一次用收银：清掉这家公司现有的收银台（与它们的班次）
    terminals = frappe.get_all("ERP POS Terminal", filters={{"company": {company!r}}}, pluck="name")
    for shift in frappe.get_all("ERP Shift", filters={{"terminal": ["in", terminals or [""]]}}, pluck="name"):
        frappe.db.delete("ERP Shift", {{"name": shift}})
    for name in terminals:
        frappe.db.delete("ERP POS Terminal", {{"name": name}})
main = warehouses.main_of({company!r})
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": "cashier@erp.regression"}}, pluck="name"):
    frappe.db.set_value("ERP Shift", shift, "status", "closed", update_modified=False)
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=main, submit=True,
                       lines=[{{"product": {product!r}, "qty": 3, "unit_cost": 10}}])
p = frappe.db.get_value("ERP Product", {product!r}, ["barcode", "code", "unit_price"], as_dict=True)
qty = frappe.db.get_value("ERP Stock Balance", {{"product": {product!r}, "warehouse": main, "location": ""}}, "qty_on_hand") or 0
frappe.db.commit()
print(json.dumps({{"main": main, "scan": p.barcode or p.code, "price": float(p.unit_price or 100), "before": float(qty)}}))
""")
    return {**out, "company": company, "product": product, "first": first, "uuid": uuid.uuid4().hex, "cases": []}


def steps(params, camera_code=None):
    scan, product, price, uid = (json.dumps(params[k]) for k in ("scan", "product", "price", "uuid"))
    api = "'pearnly_erp_portal.cashier.api."
    return [
        {"name": "打开收银页", "js": IDLE + "await idle(); await new Promise(r=>setTimeout(r,1500));"
         "const t=(document.querySelector('.page-container[data-page-route]:not([style*=\"display: none\"])')||document.body).innerText;"
         "return {no_warehouse_error: t.includes(__('This terminal has no warehouse; set one in the terminal settings')), text: t.slice(0,160)};"},
        {"name": "查码", "js": IDLE + f"const r=await frappe.call({{method:{api}api_counter_lookup', args:{{value:{scan}}}}});"
         "const m=r.message||{}; return {product: (m.product||m).name||m.name||'', found: JSON.stringify(m).slice(0,300)};"},
        {"name": "开班", "js": IDLE + f"const r=await frappe.call({{method:{api}api_open_shift', args:{{opening_float:0}}, type:'POST'}});"
         "const s=r.message||{}; return {shift: s.name||'', terminal: s.terminal||''};"},
        {"name": "卖一件", "js": IDLE +
         f"const r=await frappe.call({{method:{api}api_create_sale', type:'POST', args:{{lines:[{{product:{product}, qty:1, unit_price:{price}}}],"
         f" payments:[{{method:'cash', amount:{price}}}], client_uuid:{uid}}}}});"
         "const s=r.message||{}; return {sale: s.name||'', status: s.status||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    page, found, shift, sale = (by.get(k) or {} for k in ("打开收银页", "查码", "开班", "卖一件"))
    return [
        {"case": "cashier page has no missing warehouse error", "seen": page, "ok": page.get("no_warehouse_error") is False},
        {"case": "lookup finds the product", "seen": found, "ok": params["product"] in (found.get("found") or "")},
        {"case": "shift opened", "seen": shift, "ok": bool(shift.get("shift"))},
        {"case": "sale completed", "seen": sale, "ok": bool(sale.get("sale"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
sale = frappe.db.get_value("ERP Sale", {{"client_uuid": {params['uuid']!r}}}, ["name", "status", "terminal", "warehouse"], as_dict=True) or {{}}
terminal = frappe.db.get_value("ERP POS Terminal", sale.get("terminal"), "warehouse") if sale else None
qty = frappe.db.get_value("ERP Stock Balance", {{"product": {params['product']!r}, "warehouse": {params['main']!r}, "location": ""}}, "qty_on_hand") or 0
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": "cashier@erp.regression"}}, pluck="name"):
    frappe.db.set_value("ERP Shift", shift, "status", "closed", update_modified=False)  # 收尾：别留开着的班
frappe.db.commit()
print(json.dumps({{"sale": sale, "terminal_warehouse": terminal, "after": float(qty)}}, default=str))
""")
    return [
        {"case": "terminal sells from the main warehouse", "saved": got, "ok": got["terminal_warehouse"] == params["main"]
         and got["sale"].get("warehouse") == params["main"]},
        {"case": "stock went down by 1", "saved": [params["before"], got["after"]], "ok": got["after"] == params["before"] - 1},
    ]
