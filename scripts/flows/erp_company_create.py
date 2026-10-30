"""流程用例：ERP 新建公司（一整页：主体资料、收银与收款方式、多个仓、多台收银台，提交一次建完）（演示准备 10-30）。

管理员在公司列表点「新建公司」，照人的填法：公司名称、公司类型普通、两个仓（主仓 + 分仓）、一台收银台不指定仓，只收现金和转账，提交。
断言页面提示已新建、回到列表看得到这家公司；库里有这家公司、两个仓（主仓先建）、收银台挂主仓、收款方式只有现金和转账、类型普通。
"""
from __future__ import annotations

import json
import uuid

from desk_list import IDLE

TITLE = "ERP 新建公司：一页配齐仓库、收银台、收款方式（演示准备）"
# 截住页面画表单用的 FieldGroup（新建公司是一整页，不是对话框），同 `erp_company_settings`
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6].upper()
    return {"name": f"บริษัท ทดลองสาธิต {tag} จำกัด", "tag": tag, "branch": f"สาขา {tag}", "cases": []}


def steps(params, camera_code=None):
    name, branch = json.dumps(params["name"]), json.dumps(params["branch"])
    return [
        {"name": "打开新建公司", "js": IDLE + CAPTURE +
         "frappe.set_route('pearnly-company'); await idle(); await new Promise(r=>setTimeout(r,600)); "
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent&&e.innerText.trim()==='新建公司'); "
         "if(!b) return {error:'no new company button'}; b.click(); "
         "for(let k=0;k<60&&!window.__fgs.find(f=>f.fields_dict&&f.fields_dict.warehouses);k++) await new Promise(r=>setTimeout(r,250)); await idle(); "
         "const f=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.warehouses).pop(); if(!f) return {error:'no company form'}; "
         "return {route: frappe.get_route_str(), type: f.get_value('company_type'), warehouses: (f.get_value('warehouses')||[]).map(r=>r.warehouse_name), "
         "terminals: (f.get_value('terminals')||[]).map(r=>r.terminal_name)};"},
        {"name": "填写并提交", "js": IDLE +
         "const f=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.warehouses).pop(); if(!f) return {error:'no company form'}; "
         f"await f.set_value('company_name', {name}); await f.set_value('company_type', 'general'); "
         "await f.set_value('method_card', 0); await f.set_value('method_promptpay', 0); "
         # 仓库表照人的做法加一行：点表格的「添加行」，在新行里填仓名（直接给表格整体赋值不会进表格的数据）
         "const g=f.fields_dict.warehouses.grid; const add=g.wrapper.find('.grid-add-row')[0]; if(add) add.click(); else g.add_new_row(); "
         "await new Promise(r=>setTimeout(r,400)); const rows=g.get_data(); const last=rows[rows.length-1]; "
         f"last.warehouse_name={branch}; g.refresh(); await new Promise(r=>setTimeout(r,300)); "
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent); b.click(); "
         "await new Promise(r=>setTimeout(r,800)); await idle(); await new Promise(r=>setTimeout(r,800)); "
         "const alerts=[...document.querySelectorAll('.alert-message, .desk-alert')].map(e=>e.innerText.trim()); "
         f"return {{route: frappe.get_route_str(), alerts, listed: document.body.innerText.includes({name})}};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("打开新建公司") or {}, by.get("填写并提交") or {}
    return [
        {"case": "新建页默认：类型普通、一个主仓、一台收银台", "seen": opened,
         "ok": opened.get("type") == "general" and opened.get("warehouses") == ["主仓"] and len(opened.get("terminals") or []) == 1},
        {"case": "提示已新建、回到列表看得到这家公司", "seen": done,
         "ok": any("已新建" in a for a in done.get("alerts") or []) and done.get("route") == "pearnly-company" and bool(done.get("listed"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
co = frappe.db.get_value("ERP Company", {{"company_name": {params['name']!r}}}, ["name", "company_type"], as_dict=True) or {{}}
whs = frappe.get_all("ERP Warehouse", filters={{"company": co.get("name") or ""}}, fields=["name", "warehouse_name"], order_by="creation asc")
terms = frappe.get_all("ERP POS Terminal", filters={{"company": co.get("name") or ""}}, fields=["terminal_name", "warehouse"])
from pearnly_erp_portal.masters import company as companies
methods = sorted(companies.configured_payment_methods(co.get("name"))) if co.get("name") else []
print(json.dumps({{"company": co, "warehouses": whs, "terminals": terms, "methods": methods}}, default=str))
""")
    whs = got["warehouses"]
    names = [w["warehouse_name"] for w in whs]
    return [
        {"case": "库里有这家公司、类型普通", "saved": got["company"], "ok": got["company"].get("company_type") == "general"},
        {"case": "两个仓、主仓先建", "saved": names, "ok": names == ["主仓", params["branch"]]},
        {"case": "收银台挂主仓", "saved": got["terminals"],
         "ok": len(got["terminals"]) == 1 and bool(whs) and got["terminals"][0]["warehouse"] == whs[0]["name"]},
        {"case": "收款方式只有现金和转账", "saved": got["methods"], "ok": got["methods"] == ["cash", "transfer"]},
    ]
