"""流程用例：ERP 仓库编号与库位说明（盘点菜单做完整 S5，用户 10-29 批复 #3 #4）。

每套数据在仓库页打开数据清单里第一个仓：
- 新开户（接了 Express 的仓）：编号一格就是 Express 仓库码、只读；
- 老数据（本地仓）：填一个编号保存，库里记下、仓库列表那一列显示它。
- 仓库列表「รหัสคลัง」那一栏是服务端给的 `display_code`（主会话 11-09，与收发存导出同一个值）：没填编号的仓显示系统号。
两套都给第一个库位写说明与仓位编码（11-07）保存：库里记下；库位选择框的选项显示「编码 · 仓位 · 说明」，按编码搜得到这个仓位。
没有库位清单的仓先建一个库位再写。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 仓库：仓库编号、库位说明与仓位编码"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import locations
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
wh = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, fields=["name", "warehouse_name", "express_code", "warehouse_code"],
                    order_by="creation asc", limit=1)[0]
rows = frappe.get_all("ERP Warehouse Location", filters={{"parent": wh.name, "parenttype": "ERP Warehouse"}}, pluck="location_name", order_by="idx asc")
if not rows:
    locations.api_save_locations(warehouse=wh.name, rows=[{{"location_name": "F-01"}}])
    rows = ["F-01"]
frappe.db.set_value("ERP Warehouse", wh.name, "warehouse_code", None, update_modified=False)
frappe.db.commit()
print(json.dumps({{"warehouse": wh.name, "warehouse_name": wh.warehouse_name, "express_code": wh.express_code or "", "location": rows[0]}}))
""")
    tag = uuid.uuid4().hex[:4].upper()
    return {**out, "company": company, "code": "WF" + tag, "note": "ชั้นหน้า " + tag, "location_code": "L" + tag, "cases": []}


def steps(params, camera_code=None):
    name, code, note, loc, loc_code = (json.dumps(params[k]) for k in ("warehouse_name", "code", "note", "location", "location_code"))
    return [
        {"name": "打开仓库", "js": IDLE + CAPTURE +
         f"const a=[...document.querySelectorAll('a, .dt-cell__content')].find(e=>e.offsetParent&&e.children.length===0&&e.textContent.trim()==={name});"
         "if(!a) return {error:'warehouse row not found'}; const n=window.__dlgs.length; a.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250));"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d||!d.fields_dict.warehouse_code) return {error:'no warehouse dialog'};"
         "for(let k=0;k<40&&!(d.fields_dict.locations.grid.get_data()||[]).length;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {code: d.get_value('warehouse_code')||'', code_read_only: !!d.fields_dict.warehouse_code.df.read_only,"
         " columns: (d.fields_dict.locations.grid.docfields||[]).map(f=>f.fieldname),"
         # 仓库列表有「仓库编号」这一列：按列表格子的字段名认（整页文字里找列名，开着的弹窗自己就有这个标签，判不出列）
         " list_has_code_column: !!document.querySelector('.result .list-row-container [data-fieldname=\"display_code\"]'),"
         " list_codes: [...document.querySelectorAll('.result .list-row-container [data-fieldname=\"display_code\"]')].map(e=>e.textContent.trim())};"},
        {"name": "填编号与库位说明保存", "js": IDLE +
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d) return {error:'no dialog'};"
         f"if(!d.fields_dict.warehouse_code.df.read_only) await d.set_value('warehouse_code', {code});"
         f"const row=(d.fields_dict.locations.grid.get_data()||[]).find(r=>r.location_name==={loc}); if(!row) return {{error:'location row missing'}};"
         f"row.description={note}; row.location_code={loc_code}; d.fields_dict.locations.grid.refresh(); await new Promise(r=>setTimeout(r,300));"
         "d.get_primary_btn().trigger('click'); for(let k=0;k<40&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {closed: !d.$wrapper.is(':visible')};"},
        {"name": "库位选项", "js": IDLE +
         f"const r=await frappe.call({{method:'pearnly_erp_portal.inventory.locations.api_location_options', args:{{warehouse:{json.dumps(params['warehouse'])}}}}});"
         f"const by=await frappe.call({{method:'pearnly_erp_portal.inventory.locations.api_location_options', args:{{warehouse:{json.dumps(params['warehouse'])}, txt:{loc_code}.toLowerCase()}}}});"
         "const shape=o=>typeof o==='string'?{value:o,label:o}:o;"
         "return {options: (r.message||[]).map(shape), by_code: (by.message||[]).map(shape).map(o=>o.value)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, options = by.get("打开仓库", {}), by.get("库位选项", {}).get("options") or []
    by_code = by.get("库位选项", {}).get("by_code")
    if params["express_code"]:
        code_ok = opened.get("code") == params["express_code"] and opened.get("code_read_only")
    else:
        code_ok = not opened.get("code_read_only")
    label = next((o.get("label") for o in options if o.get("value") == params["location"]), None)
    # prepare 清了手填编号：列表那一栏应是 Express 仓库码，没有就是系统号（不是仓名）
    shown = params["express_code"] or params["warehouse"]
    return [
        {"case": "code field", "seen": opened, "ok": bool(code_ok) and {"description", "location_code"} <= set(opened.get("columns") or [])
         and opened.get("list_has_code_column")},
        {"case": "list shows the server code", "seen": opened.get("list_codes"), "ok": shown in (opened.get("list_codes") or [])},
        {"case": "saved", "seen": by.get("填编号与库位说明保存"), "ok": (by.get("填编号与库位说明保存") or {}).get("closed") is True},
        {"case": "location option shows code, name and description", "seen": options,
         "ok": label == f"{params['location_code']} · {params['location']} · {params['note']}"},
        {"case": "location found by its code", "seen": by_code, "ok": by_code == [params["location"]]},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP Warehouse", {params['warehouse']!r}, ["warehouse_code", "express_code"], as_dict=True)
loc = frappe.db.get_value("ERP Warehouse Location", {{"parent": {params['warehouse']!r}, "location_name": {params['location']!r}}},
                         ["description", "location_code"], as_dict=True)
print(json.dumps({{"warehouse": row, "description": loc.description, "location_code": loc.location_code}}))
""")
    want_code = None if params["express_code"] else params["code"]
    return [{"case": "code and description saved", "saved": got,
             "ok": (got["warehouse"]["warehouse_code"] or None) == want_code and got["description"] == params["note"]
             and got["location_code"] == params["location_code"]}]
