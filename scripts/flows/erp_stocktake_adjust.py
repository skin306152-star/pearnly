"""流程用例：盘点结束不动库存，按盘点结果生成物料调整单草稿，提交才入账（岳父 11-06）。

准备：仓 A 里一件物料账面 5，开一份盘点、记实盘 3（差 -2）。页面上：盘点员打开这份盘点 → 「结束盘点」→ 确认：盘点「已结束」、
库存还是 5、没有新流水、不排 Express；页头出现「按盘点结果调整物料」→ 点它：跳到物料调整页、打开那张草稿（一行 -2）→ 提交：
盘点「已过账」、记着这张调整单（号 IA），库存变 3。库里再验：作废这张调整单 → 库存回 5、盘点回到「已结束」、可以再生成；
新生成的草稿删掉 → 盘点仍是「已结束」、没有有效调整单。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TITLE = "ERP 盘点 → 物料调整：结束不动库存、生成草稿、提交入账、作废后再生成"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")
# 页头按钮：电脑宽度在页头，手机宽度收进「⋯」菜单（隐藏的那份 innerText 为空，按 textContent 认，点击照样生效）
BUTTON = ("const button=key=>{const label=__(key);return [...document.querySelectorAll('.page-head button, .page-head .dropdown-item, .page-actions button')]"
          ".find(e=>e.textContent.trim()===label)};")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    products = manifest.get("products")
    product = products["by_code"]["product"] if isinstance(products, dict) else products[0]["name"]
    title = "flow-ia-count-" + uuid.uuid4().hex[:8]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.access import menu
from pearnly_erp_portal.inventory import count, documents, warehouses
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
express = frappe.db.get_value("ERP Company", {company!r}, "express_enabled")
frappe.db.set_value("ERP Company", {company!r}, "express_enabled", 0, update_modified=False)
user = "stock@erp.regression"
for key in ("stock-adjust", "stock-count"):
    if not frappe.db.exists("Has Role", {{"parent": user, "role": menu.role(key)}}):
        person = frappe.get_doc("User", user); person.flags.ignore_permissions = True  # 测试准备：管理员账号不能改别人的角色
        person.add_roles(menu.role(key))
# 一个干净的仓：这一遍只有这件物料（快照只这一行）
a = warehouses.create_warehouse(warehouse_name="flow count " + frappe.generate_hash(length=4), company={company!r})["name"]
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=a, submit=True,
                       lines=[{{"product": {product!r}, "qty": 5, "unit_cost": 10, "location": ""}}])
task = count.create_count(title={title!r}, warehouse=a)["name"]
count.add_entry(count=task, product={product!r}, qty=3, warehouse=a, location="", request_id="flow-ia-count-" + task)
frappe.db.commit()
print(json.dumps({{"a": a, "task": task, "express": int(express or 0)}}))
""")
    return {**out, "company": company, "product": product, "title": title, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "结束盘点", "js": IDLE + CAPTURE + BUTTON +
         "await idle(); const b=button('stock.count.close'); if(!b) return {error:'no close button'};"
         "const n=window.__dlgs.length; b.click(); for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250));"
         "const c=window.__dlgs[window.__dlgs.length-1]; c.get_primary_btn().trigger('click');"
         "for(let k=0;k<60&&!button('stock.count.adjust');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {adjust_button: Boolean(button('stock.count.adjust')), close_gone: !button('stock.count.close')};"},
        {"name": "按盘点结果调整物料", "js": IDLE + CAPTURE + BUTTON +
         "const b=button('stock.count.adjust'); if(!b) return {error:'no adjust button'}; const n=window.__dlgs.length; b.click();"
         "for(let k=0;k<60&&(window.__dlgs.length===n||frappe.get_route()[0]!=='pearnly-stock-adjust');k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); const d=window.__dlgs[window.__dlgs.length-1]; if(!d) return {error:'no draft dialog'};"
         "const qty=[...d.$wrapper.find('.pearnly-line-editor [data-fieldname=\"qty\"] input')].map(e=>e.value);"
         "return {route: frappe.get_route()[0], editable: Boolean(d.get_primary_btn().is(':visible')), qty};"},
        {"name": "提交调整单", "js": IDLE +
         "const d=window.__dlgs.filter(x=>x.$wrapper.is(':visible')).pop(); if(!d) return {error:'dialog gone'};"
         "d.get_primary_btn().trigger('click');"
         "for(let k=0;k<80&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {submitted: !d.$wrapper.is(':visible')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    closed, draft = by.get("结束盘点") or {}, by.get("按盘点结果调整物料") or {}
    return [
        {"case": "after closing the count offers to adjust products", "seen": closed,
         "ok": closed.get("adjust_button") is True and closed.get("close_gone") is True},
        {"case": "the button opens the draft adjustment with the difference", "seen": draft,
         "ok": draft.get("route") == "pearnly-stock-adjust" and draft.get("editable") is True and "-2" in (draft.get("qty") or [])},
        {"case": "draft submitted", "seen": by.get("提交调整单"), "ok": (by.get("提交调整单") or {}).get("submitted") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count, documents
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({params['company']!r})
a, product, task = {params['a']!r}, {params['product']!r}, {params['task']!r}
def qty():
    return float(frappe.db.get_value("ERP Stock Balance", {{"product": product, "warehouse": a, "location": ""}}, "qty_on_hand") or 0)
state = frappe.db.get_value("ERP Stock Count", task, ["status", "adjustment", "ledger_state"], as_dict=True)
entry = frappe.db.get_value("ERP Stock Entry", state.adjustment, ["name", "direction", "status", "doc_no", "stock_count", "ref_type"], as_dict=True) if state.adjustment else {{}}
moves = frappe.get_all("ERP Stock Movement", filters={{"ref_id": state.adjustment or "-"}}, fields=["movement_type", "qty_base"])
posted = qty()
documents.void_entry(state.adjustment)
after_void = [frappe.db.get_value("ERP Stock Count", task, "status"), frappe.db.get_value("ERP Stock Count", task, "adjustment"), qty()]
again = count.adjustment_draft(task)
again_state = frappe.db.get_value("ERP Stock Count", task, ["status", "adjustment"], as_dict=True)
documents.delete_draft(again["name"])
after_delete = [frappe.db.get_value("ERP Stock Count", task, "status"), frappe.db.get_value("ERP Stock Count", task, "adjustment"),
                bool(frappe.db.exists("ERP Stock Entry", again["name"]))]
frappe.db.set_value("ERP Company", {params['company']!r}, "express_enabled", {params['express']}, update_modified=False)  # 还原
frappe.db.commit()
print(json.dumps({{"state": state, "entry": entry, "moves": [dict(m, qty_base=float(m.qty_base)) for m in moves], "posted": posted,
                  "after_void": after_void, "again": again, "again_state": again_state, "after_delete": after_delete}}, default=str))
""")
    state, entry, moves = got["state"], got["entry"], got["moves"]
    return [
        {"case": "count posted through one IA adjustment that points back to it", "saved": [state, entry],
         "ok": state.get("status") == "Posted" and entry.get("direction") == "adjust" and entry.get("status") == "submitted"
         and str(entry.get("doc_no") or "").startswith("IA") and entry.get("stock_count") == params["task"] and not entry.get("ref_type")
         and not state.get("ledger_state")},
        {"case": "the adjustment moved -2 and stock is the counted 3", "saved": [moves, got["posted"]],
         "ok": [(m["movement_type"], m["qty_base"]) for m in moves] == [("adjust", -2.0)] and got["posted"] == 3},
        {"case": "voiding the adjustment puts stock back and reopens the count for a new adjustment", "saved": got["after_void"],
         "ok": got["after_void"][0] == "Closed" and not got["after_void"][1] and got["after_void"][2] == 5},
        {"case": "a new draft can be made and deleted again", "saved": [got["again"], got["again_state"], got["after_delete"]],
         "ok": got["again"].get("created") is True and got["again_state"].get("adjustment") == got["again"].get("name")
         and got["after_delete"][0] == "Closed" and not got["after_delete"][1] and got["after_delete"][2] is False},
    ]
