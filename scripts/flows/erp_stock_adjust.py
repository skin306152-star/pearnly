"""流程用例：ERP 物料调整（岳父 11-06，号 IA）——一张单正负混录、增加行默认带均价可改、减少行单价只读、收发存分收入 / 发出、作废冲回。

库存岗位打开「物料调整」页新建一张：单头仓 A，第一行按码填物料、数量 3（单价默认带出当前均价，改成 7），第二行同一物料、数量 -2
（单价那一格只读），备注一个识别串，提交。库里：一张已提交的调整单（号 IA…），两笔 `adjust` 流水 +3 / -2，第一行记「人填的价」；
收发存里这件物料本期收入 3、发出 2；入库 / 出库列表不出现盘点生成的旧 AD 单、物料调整列表出现；作废后回原数。
Express：关了「接 Express」的公司提交不排账本；开着且接上的公司提交后排进账本（一张调整单写一张 TK，由 CC 写，这里只看排没排）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import ledger_case  # noqa: E402

TITLE = "ERP 物料调整：正负混录、默认均价、收发存两栏、作废冲回"
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
    # 这一格关掉「接 Express」（核对时还会先开后关）；格结束时 run_flow 还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", company, {"express_enabled": 0})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.access import menu
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
user = "stock@erp.regression"
if not frappe.db.exists("Has Role", {{"parent": user, "role": menu.role("stock-adjust")}}):  # 老数据建库时还没有物料调整菜单
    person = frappe.get_doc("User", user); person.flags.ignore_permissions = True  # 测试准备：管理员账号不能改别人的角色
    person.add_roles(menu.role("stock-adjust"))
a = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
p = frappe.db.get_value("ERP Product", {product!r}, ["name", "code"], as_dict=True)
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=a, submit=True,
                       lines=[{{"product": p.name, "qty": 5, "unit_cost": 10, "location": ""}}])
row = frappe.db.get_value("ERP Stock Balance", {{"product": p.name, "warehouse": a, "location": ""}}, ["qty_on_hand"], as_dict=True) or {{}}
frappe.db.commit()
print(json.dumps({{"product": p.name, "scan": {scan!r} or p.code, "a": a, "before": float(row.get("qty_on_hand") or 0)}}))
""")
    return {**out, "company": company, "token": "flow-ia-" + uuid.uuid4().hex[:8], "cases": []}


def _fill(scan, qty):
    """在最后一条明细里按码填物料（扫码枪回车那条路）并填数量；回这一条的 FieldGroup。"""
    return ("const lines=()=>window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.qty&&f.fields_dict.unit_cost&&$(f.wrapper).is(':visible'));"
            "const l=lines().pop(); if(!l) return {error:'no line'};"
            "const i=l.fields_dict.product.$input[0];"
            f"i.focus(); i.value={scan}; i.dispatchEvent(new Event('input',{{bubbles:true}}));"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
            "for(let k=0;k<40&&!l.get_value('product');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            f"await l.set_value('qty', {qty}); await idle();")


def steps(params, camera_code=None):
    a, scan, token = (json.dumps(params[k]) for k in ("a", "scan", "token"))
    return [
        {"name": "新建物料调整单", "js": IDLE + CAPTURE +
         "const btn=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!btn) return {error:'no new button'}; const n=window.__dlgs.length; btn.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d||!d.fields_dict.warehouse) return {error:'no dialog'};"
         f"await d.set_value('warehouse', {a}); await d.set_value('reason', {token}); await idle();"
         + _fill(scan, 3) +
         "const shown=l.get_value('unit_cost'); await l.set_value('unit_cost', 7); await idle();"
         "return {fields: Object.keys(d.fields_dict).filter(k=>['warehouse','location','purpose','to_warehouse'].includes(k)),"
         " line_warehouse_hidden: Boolean(l.fields_dict.warehouse && l.fields_dict.warehouse.df.hidden),"
         " product: l.get_value('product'), shown};"},
        {"name": "加一行减少", "js": IDLE +
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.add_line&&x.$wrapper.is(':visible')); if(!d) return {error:'dialog gone'};"
         "d.fields_dict.add_line.$input.trigger('click'); await idle();"
         + _fill(scan, -2) +
         "l.refresh_dependency&&l.refresh_dependency(); await idle();"
         "return {price_locked: Boolean(l.fields_dict.unit_cost.df.read_only), qty: l.get_value('qty')};"},
        {"name": "提交", "js": IDLE +
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.add_line&&x.$wrapper.is(':visible')); if(!d) return {error:'dialog gone'};"
         "d.get_primary_btn().trigger('click');"
         "for(let k=0;k<80&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "await new Promise(r=>setTimeout(r,800));"
         "const msg=[...document.querySelectorAll('.msgprint, .modal.show .modal-body')].map(e=>e.innerText).join(' | ').slice(0,400);"
         "const row=[...document.querySelectorAll('.dt-row, .list-row, .frappe-list .result')].map(e=>e.innerText).find(t=>t.includes('IA'));"
         "return {submitted: !d.$wrapper.is(':visible'), listed: Boolean(row), msg};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    made, less = by.get("新建物料调整单") or {}, by.get("加一行减少") or {}
    return [
        {"case": "adjustment dialog has warehouse and location, no purpose", "seen": made,
         "ok": {"warehouse", "location"} <= set(made.get("fields") or []) and "purpose" not in (made.get("fields") or [])
         and "to_warehouse" not in (made.get("fields") or [])},
        {"case": "line shows no warehouse (one warehouse per document)", "seen": made.get("line_warehouse_hidden"),
         "ok": made.get("line_warehouse_hidden") is True},
        {"case": "product filled by code", "seen": made.get("product"), "ok": made.get("product") == params["product"]},
        {"case": "increase line brings the average cost", "seen": made.get("shown"), "ok": made.get("shown") not in (None, "", 0)},
        {"case": "decrease line price is read only", "seen": less, "ok": less.get("price_locked") is True and less.get("qty") == -2},
        {"case": "submitted", "seen": by.get("提交"), "ok": (by.get("提交") or {}).get("submitted") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import push
from pearnly_erp_portal.inventory import documents, report
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({params['company']!r})
a, product = {params['a']!r}, {params['product']!r}
def qty():
    return float(frappe.db.get_value("ERP Stock Balance", {{"product": product, "warehouse": a, "location": ""}}, "qty_on_hand") or 0)
entry = frappe.db.get_value("ERP Stock Entry", {{"reason": {params['token']!r}}}, ["name", "direction", "status", "doc_no", "warehouse", "ledger_state"], as_dict=True) or {{}}
lines = frappe.get_all("ERP Stock Entry Line", filters={{"parent": entry.get("name") or "-"}}, fields=["qty", "unit_cost", "unit_cost_manual", "warehouse"], order_by="idx asc")
moves = frappe.get_all("ERP Stock Movement", filters={{"ref_id": entry.get("name") or "-"}}, fields=["movement_type", "qty_base", "doc_no"], order_by="creation asc")
today = frappe.utils.nowdate()
summary = [r for r in report.summary(date_from=today, date_to=today, product=product, warehouse=a) if r.get("product") == product]
after = qty()
ins = documents.list_entries(direction="in", limit=200)
adjusts = documents.list_entries(direction="adjust", limit=200)
voided = documents.void_entry(entry["name"])["status"] if entry else ""
back = qty()
# Express：开着「接 Express」的公司提交排不排看是否已接上；关着的不排
frappe.db.set_value("ERP Company", {params['company']!r}, "express_enabled", 1, update_modified=False)
linked = bool(push.linked({params['company']!r}))
on = documents.create_entry(direction="adjust", posting_date=today, warehouse=a, submit=True, reason={params['token']!r} + "-on",
                            lines=[{{"product": product, "qty": 1}}])["name"]
frappe.db.set_value("ERP Company", {params['company']!r}, "express_enabled", 0, update_modified=False)
off = documents.create_entry(direction="adjust", posting_date=today, warehouse=a, submit=True, reason={params['token']!r} + "-off",
                             lines=[{{"product": product, "qty": 1}}])["name"]
documents.void_entry(off)
frappe.db.commit()
print(json.dumps({{"entry": entry, "lines": lines, "moves": moves, "summary": summary, "after": after, "voided": voided, "back": back,
                  "in_count_rows": [r["name"] for r in ins if r.get("ref_type") == "count"],
                  "in_adjust_rows": [r["name"] for r in ins if r.get("direction") == "adjust"],
                  "adjust_kinds": sorted({{(r.get("direction"), r.get("ref_type") or "") for r in adjusts}}),
                  "linked": linked, "on": on, "off": off}}, default=str))
""")
    entry, lines, moves = got["entry"], got["lines"], got["moves"]
    summary = got["summary"][0] if got["summary"] else {}
    return [
        {"case": "a submitted IA adjustment in warehouse A", "saved": entry,
         "ok": entry.get("direction") == "adjust" and entry.get("status") == "submitted" and str(entry.get("doc_no") or "").startswith("IA")
         and entry.get("warehouse") == params["a"]},
        {"case": "lines keep their signs, the typed price is marked, the decrease keeps no price", "saved": lines,
         "ok": [float(r["qty"]) for r in lines] == [3.0, -2.0] and float(lines[0]["unit_cost"] or 0) == 7 and lines[0]["unit_cost_manual"] == 1
         and not lines[1]["unit_cost_manual"] and all(r["warehouse"] == params["a"] for r in lines)},
        {"case": "two adjust movements +3 and -2 carry the IA number", "saved": moves,
         "ok": [(m["movement_type"], float(m["qty_base"])) for m in moves] == [("adjust", 3.0), ("adjust", -2.0)]
         and all(str(m["doc_no"]).startswith("IA") for m in moves)},
        {"case": "stock summary puts the increase in receipts and the decrease in issues", "saved": summary,
         "ok": float(summary.get("in_qty") or 0) >= 3 and float(summary.get("out_qty") or 0) >= 2},
        {"case": "stock goes up 1 and comes back on void", "saved": [params["before"], got["after"], got["voided"], got["back"]],
         "ok": got["after"] == params["before"] + 1 and got["voided"] == "void" and got["back"] == params["before"]},
        {"case": "stock-in list has no count entries and no adjustments", "saved": [got["in_count_rows"], got["in_adjust_rows"]],
         "ok": not got["in_count_rows"] and not got["in_adjust_rows"]},
        {"case": "adjustment list has only adjustments and old count entries", "saved": got["adjust_kinds"],
         "ok": all(d == "adjust" or ref == "count" for d, ref in got["adjust_kinds"])},
        # 接了 Express 的公司（已接上）排进账本；关了「接 Express」的不排
        ledger_case(lab, manifest["site"], "ERP Stock Entry", [got["on"]], got["linked"]),
        ledger_case(lab, manifest["site"], "ERP Stock Entry", [got["off"]], False),
    ]
