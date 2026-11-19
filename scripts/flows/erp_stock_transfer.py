"""流程用例：ERP 调拨（多仓闭环 P2：同公司两仓之间，一出一进、成本随货走、作废两边冲回；按台商品选台换仓）。

库存岗位打开「调拨」页新建一张：单头调出仓 A、调入仓 B，明细按码填商品（扫码枪回车那条路）、数量 2、备注一个识别串，提交。
库里：一张已提交的调拨单（号 TF…），调出流水 −2 在 A、调入流水 +2 在 B、调入单价 = 调出单价，A 少 2、B 多 2、两仓合计金额不变；
作废后两仓回到原数。按台：公司临时设成汽车，入库登记一台车到 A，调拨把这台调到 B（台换仓），同仓调拨被拒，作废后台回 A；跑完还原。
新开户那家公司接了 Express：这条流程只看 ERP 调拨本身，跑的时候先关掉「接 Express」（不走写账预检），跑完还原。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import ledger_case  # noqa: E402

TITLE = "ERP 调拨：两仓之间调货、作废冲回、按台换仓"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}"
           "window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const f=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return f.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    products = manifest.get("products")
    if isinstance(products, dict):
        product, scan = products["by_code"]["product"], products["by_code"]["scan"]
    else:
        product, scan = products[0]["name"], products[0].get("scan") or ""
    # 这一格关掉「接 Express」，核对时还把公司临时改成按台；格结束时 run_flow 都还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", company, {"express_enabled": 0})
    lab.keep_fields(manifest["site"], "ERP Company", company, ["company_type"])
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.access import menu
from pearnly_erp_portal.inventory import documents, stock
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
user = "stock@erp.regression"
if not frappe.db.exists("Has Role", {{"parent": user, "role": menu.role("stock-transfer")}}):  # 老数据建库时还没有调拨菜单
    frappe.get_doc("User", user).add_roles(menu.role("stock-transfer"))
active = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=2)
if len(active) < 2:
    from pearnly_erp_portal.inventory import warehouses
    active.append(warehouses.create_warehouse(warehouse_name="flow transfer " + frappe.generate_hash(length=4), company={company!r})["name"])
a, b = active[0], active[1]
p = frappe.db.get_value("ERP Product", {product!r}, ["name", "code", "barcode"], as_dict=True)
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=a, submit=True,
                       lines=[{{"product": p.name, "qty": 5, "unit_cost": 10}}])
def place(w):
    row = frappe.db.get_value("ERP Stock Balance", {{"product": p.name, "warehouse": w}}, ["qty_on_hand", "value"], as_dict=True) or {{}}
    return [float(row.get("qty_on_hand") or 0), float(row.get("value") or 0)]
frappe.db.commit()
print(json.dumps({{"product": p.name, "scan": {scan!r} or p.code, "a": a, "b": b, "before_a": place(a), "before_b": place(b)}}))
""")
    return {**out, "company": company, "token": "flow-tf-" + uuid.uuid4().hex[:8], "cases": []}


def steps(params, camera_code=None):
    a, b, scan, token = (json.dumps(params[k]) for k in ("a", "b", "scan", "token"))
    return [
        {"name": "新建调拨单", "js": IDLE + CAPTURE +
         "const btn=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!btn) return {error:'no new button'}; const n=window.__dlgs.length; btn.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d||!d.fields_dict.to_warehouse) return {error:'no transfer dialog'};"
         f"await d.set_value('warehouse', {a}); await d.set_value('to_warehouse', {b}); await d.set_value('reason', {token}); await idle();"
         "const i=d.$wrapper.find('.pearnly-line-editor [data-fieldname=\"product\"] input')[0]; if(!i) return {error:'no line product box'};"
         f"i.focus(); i.value={scan}; i.dispatchEvent(new Event('input',{{bubbles:true}}));"
         "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
         "const line=()=>window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.warehouse&&f.fields_dict.qty&&f.fields_dict.product).pop();"
         "for(let k=0;k<40&&!(line()&&line().get_value('product'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const l=line(); if(!l||!l.get_value('product')) return {error:'product not filled by code'};"
         "await l.set_value('qty', 2); await idle();"
         "return {fields: Object.keys(d.fields_dict).filter(k=>['warehouse','location','to_warehouse','to_location','purpose'].includes(k)),"
         " line_fields: Object.keys(l.fields_dict).filter(k=>['location','to_location','warehouse'].includes(k)), product: l.get_value('product')};"},
        {"name": "同仓被拒", "js": IDLE +
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.to_warehouse&&x.$wrapper.is(':visible')); if(!d) return {error:'dialog gone'};"
         f"await d.set_value('to_warehouse', {a}); await idle();"
         "const before=d.$wrapper.is(':visible'); d.get_primary_btn().trigger('click');"
         "for(let k=0;k<20&&!document.querySelector('.msgprint-dialog.show, .modal.show .msgprint');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const msg=[...document.querySelectorAll('.msgprint')].map(e=>e.innerText).join(' | ');"
         "document.querySelectorAll('.msgprint-dialog .btn-modal-close').forEach(x=>x.click()); await new Promise(r=>setTimeout(r,400));"
         f"await d.set_value('to_warehouse', {b}); await idle();"
         "return {still_open: before && d.$wrapper.is(':visible'), msg};"},
        {"name": "提交", "js": IDLE +
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.to_warehouse&&x.$wrapper.is(':visible')); if(!d) return {error:'dialog gone'};"
         "d.get_primary_btn().trigger('click');"
         "for(let k=0;k<80&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "await new Promise(r=>setTimeout(r,800));"
         f"const row=[...document.querySelectorAll('.dt-row, .list-row, .frappe-list .result')].map(e=>e.innerText).find(t=>t.includes('TF'));"
         "return {submitted: !d.$wrapper.is(':visible'), listed: Boolean(row)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    made = by.get("新建调拨单") or {}
    return [
        # 单头一出一进（同 Express 原生 RL，11-19 取消仓位）：单头调出仓 / 调入仓，没有仓位、没有用途；行上不放仓位
        {"case": "transfer dialog has both warehouses, no locations and no purpose", "seen": made,
         "ok": {"warehouse", "to_warehouse"} <= set(made.get("fields") or [])
         and not {"location", "to_location", "purpose"} & set(made.get("fields") or [])
         and not {"location", "to_location"} & set(made.get("line_fields") or [])},
        {"case": "product filled by code", "seen": made.get("product"), "ok": made.get("product") == params["product"]},
        {"case": "same warehouse refused", "seen": by.get("同仓被拒"), "ok": (by.get("同仓被拒") or {}).get("still_open") is True},
        {"case": "submitted", "seen": by.get("提交"), "ok": (by.get("提交") or {}).get("submitted") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents, serials
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({params['company']!r})
a, b, product = {params['a']!r}, {params['b']!r}, {params['product']!r}
def place(w):
    row = frappe.db.get_value("ERP Stock Balance", {{"product": product, "warehouse": w}}, ["qty_on_hand", "value"], as_dict=True) or {{}}
    return [float(row.get("qty_on_hand") or 0), float(row.get("value") or 0)]
entry = frappe.db.get_value("ERP Stock Entry", {{"reason": {params['token']!r}}}, ["name", "direction", "status", "doc_no", "warehouse", "to_warehouse"], as_dict=True) or {{}}
moves = frappe.get_all("ERP Stock Movement", filters={{"ref_id": entry.get("name") or "-"}}, fields=["movement_type", "warehouse", "qty_base", "unit_cost", "value_delta", "cost_known"], order_by="creation asc")
after = [place(a), place(b)]
voided = documents.void_entry(entry["name"])["status"] if entry else ""
back = [place(a), place(b)]
# 按台：公司临时设成汽车，入一台车到 A，调到 B，作废回 A
frappe.db.set_value("ERP Company", {params['company']!r}, "company_type", "vehicle", update_modified=False)
code = "FLOW-TF-CAR"
car = frappe.db.get_value("ERP Product", {{"code": code}})
if not car:
    from pearnly_erp_portal.products import service as products
    car = products.create_product(product_name="รถทดสอบ ย้ายคลัง", code=code, unit="คัน", unit_price=500000, default_cost=400000)["name"]
if not frappe.db.get_value("ERP Product", car, "track_serial"):
    serials.turn_on(car, kind="vehicle")
vin = "MR0TF" + frappe.generate_hash(length=12).upper()
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=a, purpose="other", submit=True,
                       lines=[{{"product": car, "qty": 1, "unit_cost": 400000, "serials": [{{"serial_no": vin}}]}}])
unit = frappe.db.get_value("ERP Serial No", {{"serial_no": vin}}, "name")
same = ""
try:
    documents.create_entry(direction="transfer", posting_date=frappe.utils.nowdate(), warehouse=a, to_warehouse=a, submit=True,
                           lines=[{{"product": car, "qty": 1, "serials": [{{"serial": unit}}]}}])
except Exception as exc:
    frappe.db.rollback(); same = str(exc)
moved = documents.create_entry(direction="transfer", posting_date=frappe.utils.nowdate(), warehouse=a, to_warehouse=b, submit=True,
                               lines=[{{"product": car, "qty": 1, "serials": [{{"serial": unit}}]}}])
at_b = frappe.db.get_value("ERP Serial No", unit, ["warehouse", "status"], as_dict=True)
documents.void_entry(moved["name"])
at_a = frappe.db.get_value("ERP Serial No", unit, ["warehouse", "status"], as_dict=True)
frappe.db.commit()
print(json.dumps({{"entry": entry, "moves": [dict(m, qty_base=float(m.qty_base), unit_cost=float(m.unit_cost), value_delta=float(m.value_delta)) for m in moves],
                  "after": after, "voided": voided, "back": back, "same": same, "at_b": at_b, "at_a": at_a, "b_name": b}}, default=str))
""")
    entry, moves = got["entry"], got["moves"]
    out_, in_ = ([m for m in moves if m["movement_type"] == kind] for kind in ("transfer_out", "transfer_in"))
    before_a, before_b = params["before_a"], params["before_b"]
    return [
        {"case": "a submitted TF transfer from A to B", "saved": entry,
         "ok": entry.get("direction") == "transfer" and entry.get("status") == "submitted" and str(entry.get("doc_no") or "").startswith("TF")
         and entry.get("warehouse") == params["a"] and entry.get("to_warehouse") == params["b"]},
        {"case": "one out at A and one in at B at the same unit cost", "saved": moves,
         "ok": len(out_) == 1 and len(in_) == 1 and out_[0]["warehouse"] == params["a"] and in_[0]["warehouse"] == params["b"]
         and out_[0]["qty_base"] == -2 and in_[0]["qty_base"] == 2 and out_[0]["unit_cost"] == in_[0]["unit_cost"]
         # 流水的 value_delta 记的是金额大小（出库也是正数，方向看流水类型），一出一进金额相等
         and round(out_[0]["value_delta"], 2) == round(in_[0]["value_delta"], 2)},
        {"case": "A down 2, B up 2, total value unchanged", "saved": [before_a, before_b, got["after"]],
         "ok": got["after"][0][0] == before_a[0] - 2 and got["after"][1][0] == before_b[0] + 2
         and round(got["after"][0][1] + got["after"][1][1], 2) == round(before_a[1] + before_b[1], 2)},
        {"case": "voiding puts both warehouses back", "saved": [got["voided"], got["back"]],
         "ok": got["voided"] == "void" and got["back"][0][0] == before_a[0] and got["back"][1][0] == before_b[0]},
        {"case": "a unit moves to B and comes back on void; same warehouse refused", "saved": [got["same"], got["at_b"], got["at_a"]],
         "ok": bool(got["same"]) and (got["at_b"] or {}).get("warehouse") == got["b_name"] and (got["at_b"] or {}).get("status") == "in_stock"
         and (got["at_a"] or {}).get("warehouse") == params["a"] and (got["at_a"] or {}).get("status") == "in_stock"},
        # 账本栏是通用列；这一遍关了「接 Express」，调拨单不排进账本
        ledger_case(lab, manifest["site"], "ERP Stock Entry", [entry.get("name")], False),
    ]
