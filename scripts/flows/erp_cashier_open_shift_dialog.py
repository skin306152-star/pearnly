"""流程用例：收银页点「开班」走开班框（11-17：开户自动建的默认公司、接 Express 的公司都没有收银台，开班框「收银台」是必填下拉却没有选项，
点不了开班；`erp.cashier.first-sale` 直接调开班接口，没走这个框）。

新开户（接 Express、一台收银台都没有）：先清掉这家公司现有的收银台（只在实验室，模拟新客户），打开收银页点「开班」→ 开班框
不能卡在没选项的必填「收银台」上，点开班就开出班；库里建出默认收银台、挂主仓。
老数据（建公司时配了收银台）：开班框里收银台已带出那台，点开班照样开出班。
"""
from __future__ import annotations

import json

TITLE = "ERP 收银页点「开班」走开班框：没有收银台的新公司也开得了班"
CASHIER = "cashier@erp.regression"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};"
        "const until=async(f)=>{for(let k=0;k<60;k++){const v=f();if(v)return v;await new Promise(r=>setTimeout(r,250));}return null;};")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    first = manifest["dataset"] == "new"
    # 这一格开着收银；格结束时 run_flow 还原原值（`Lab.set_fields`）
    lab.set_fields(manifest["site"], "ERP Company", company, {"pos_enabled": 1})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import warehouses
if {first!r}:
    # 模拟新客户：清掉这家公司现有的收银台（与它们的班次）
    terminals = frappe.get_all("ERP POS Terminal", filters={{"company": {company!r}}}, pluck="name")
    for shift in frappe.get_all("ERP Shift", filters={{"terminal": ["in", terminals or [""]]}}, pluck="name"):
        frappe.db.delete("ERP Shift", {{"name": shift}})
    for name in terminals:
        frappe.db.delete("ERP POS Terminal", {{"name": name}})
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": {CASHIER!r}}}, pluck="name"):
    frappe.db.set_value("ERP Shift", shift, "status", "closed", update_modified=False)
frappe.db.commit()
print(json.dumps({{"main": warehouses.main_of({company!r}),
                  "terminals": frappe.db.count("ERP POS Terminal", {{"company": {company!r}}})}}))
""")
    return {**out, "company": company, "first": first, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "点开班", "js": IDLE + "await idle();"
         "const btn=await until(()=>document.querySelector('.pearnly-pos-empty-btn')"
         "||[...document.querySelectorAll('.page-actions .primary-action')].find(b=>b.offsetParent));"
         "if(!btn) return {error:'no open shift button'}; btn.click();"
         "const d=await until(()=>window.cur_dialog&&cur_dialog.display&&cur_dialog.fields_dict&&cur_dialog);"
         "if(!d) return {error:'no dialog'}; await idle();"
         "const f=d.fields_dict.terminal;"
         "return {terminal_field: !!f, required: !!(f&&f.df.reqd), shown: !!(f&&f.$wrapper.is(':visible')),"
         " options: f?(f.df.options||[]).length:0, value: d.get_value('terminal')||''};"},
        {"name": "开班框提交", "js": IDLE +
         "const d=window.cur_dialog; if(!d) return {error:'no dialog'}; d.get_primary_btn().click();"
         "await new Promise(r=>setTimeout(r,800)); await idle(); await new Promise(r=>setTimeout(r,800));"
         "const msg=[...document.querySelectorAll('.msgprint, .modal.show .modal-body')].map(e=>e.innerText).join(' ').slice(0,200);"
         "const r=await frappe.call({method:'pearnly_erp_portal.cashier.api.api_current_shift'});"
         "const s=(r.message&&(r.message.shift||r.message))||{};"
         "return {dialog_open: !!(d.display), shift: s.name||'', terminal: s.terminal||'', message: msg};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, sent = by.get("点开班") or {}, by.get("开班框提交") or {}
    cases = [
        {"case": "the open shift dialog shows", "seen": opened, "ok": not opened.get("error")},
        # 必填的收银台下拉不能没有选项（没有收银台时整格不要求）
        {"case": "no required terminal box without any option", "seen": opened,
         "ok": not (opened.get("required") and opened.get("shown") and not opened.get("options"))},
        {"case": "open shift in the dialog opens a shift", "seen": sent, "ok": bool(sent.get("shift")) and not sent.get("dialog_open")},
    ]
    if not params["first"]:
        cases.append({"case": "the existing terminal is filled in", "seen": opened, "ok": bool(opened.get("value"))})
    return cases


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
shift = frappe.db.get_value("ERP Shift", {{"status": "open", "cashier": {CASHIER!r}}}, ["name", "terminal"], as_dict=True) or {{}}
terminal = frappe.db.get_value("ERP POS Terminal", shift.get("terminal"), ["company", "warehouse"], as_dict=True) if shift else None
if shift:
    frappe.delete_doc("ERP Shift", shift["name"], ignore_permissions=True, force=True)  # 收尾：本格开出的班不留
    if {params['first']!r} and shift.get("terminal"):
        frappe.db.delete("ERP POS Terminal", {{"name": shift["terminal"]}})  # 新开户这格当场建出的收银台也不留
frappe.db.commit()
print(json.dumps({{"shift": shift, "terminal": terminal}}, default=str))
""")
    terminal = got.get("terminal") or {}
    return [
        {"case": "the shift is on a terminal of this company", "saved": got, "ok": terminal.get("company") == params["company"]},
        {"case": "a terminal made for a new company sells from the main warehouse", "saved": got,
         "ok": (not params["first"]) or terminal.get("warehouse") == params["main"]},
    ]
