"""流程用例：ERP 单据上的 Express 弹窗 —— 已提交的采购单点「Express」，弹窗显示写账状态；取状态走 POST
（起因：10-29 `express.api.document_express` 在转人工要人选时会按单据此刻的内容准备一遍、存回合计，GET 里白写被回滚，改成只收 POST）。

- new（公司接了 Express）：开一张采购单提交（进写账队列），打开它点「Express」→ 出弹窗、页面请求 `document_express` 用 POST
  （参数不在网址上）；库里这张单的写账状态与任务不变（只是看）。
- old（公司没接 Express）：打开已有的采购单，没有「Express」按钮、不请求这个接口。
"""
from __future__ import annotations

import json
import uuid

from desk_list import ACTION, IDLE

TITLE = "ERP 单据 Express 弹窗：取状态走 POST、只看不改"
API = "pearnly_erp_portal.express.api.document_express"


def prepare(lab, manifest):
    linked = bool(manifest.get("express_linked"))
    if linked:
        company = manifest["company"]
        warehouse = manifest["warehouses"][0]["name"]
        product = manifest["products"]["by_code"]["product"]
        out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry, suppliers
frappe.set_user("purchase@erp.regression")
companies.set_active_company({company!r})
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายทดสอบ Express"}}) or \\
    suppliers.create_supplier(supplier_name="ผู้ขายทดสอบ Express")["name"]
frappe.db.commit()
doc = entry.post({{"company": {company!r}, "supplier": supplier, "doc_date": "2026-10-20", "currency": "THB", "fx_rate": 1,
                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                  "doc_no": "FLOW-EXP-" + {uuid.uuid4().hex[:6]!r}, "warehouse": {warehouse!r},
                  "lines": [{{"product": {product!r}, "item_type": "goods", "description": "flow", "qty": 1, "unit": "ชิ้น",
                             "unit_price": 100, "vat_rate": 7, "warehouse": {warehouse!r}, "location": ""}}]}},
                 request_id="flow-express-" + {uuid.uuid4().hex!r})
frappe.db.commit()
# 实验室有后台进程：建单排进去的推送任务会把状态从 queued 推到下一步（没连电脑时停在「待处理 · 正在读取 Express 账套」）。
# 等它稳定再记「看之前」的状态，断言才只量「看弹窗」这一步（10-30：加了后台进程后这里和推送任务赛跑）
import time
for _ in range(60):
    frappe.db.rollback()
    if frappe.db.get_value("Purchase Document", doc["name"], "ledger_state") != "queued":
        break
    time.sleep(1)
row = frappe.db.get_value("Purchase Document", doc["name"], ["name", "ledger_state", "ledger_job"], as_dict=True)
print(json.dumps(row, default=str))
""")
    else:
        out = lab.python(manifest["site"], """
row = frappe.get_all("Purchase Document", filters={"status": "submitted"}, fields=["name", "ledger_state", "ledger_job"],
                     order_by="creation asc", limit_page_length=1)[0]
print(json.dumps(row, default=str))
""")
    return {"doc": out["name"], "linked": linked, "state": out.get("ledger_state") or "", "job": out.get("ledger_job") or ""}


def steps(params, camera_code=None):
    # 点之前包一层 $.ajax 记下请求方法（`p.call` 经 frappe.call 走 $.ajax；同验收窗口 10-29 的实点做法）
    hook = ("window.__calls=[]; const _ajax=$.ajax; $.ajax=function(o){ if(o&&String(o.url||'').includes(API)) "
            "window.__calls.push(String(o.type||o.method||'GET').toUpperCase()); return _ajax.apply(this, arguments); };").replace(
        "API", json.dumps(API))
    return [{"name": "Express 弹窗", "js": IDLE + ACTION + "await idle();"
             "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             + hook +
             "const via=await action('Express'); if(!via) return {button:false, calls:window.__calls};"
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const body=cur_dialog&&cur_dialog.$wrapper.is(':visible')?(cur_dialog.$body.text()||'').replace(/\\s+/g,' ').trim():'';"
             "return {button:true, via, dialog: body.slice(0, 300), calls:window.__calls};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "Express 弹窗"), None) or {}
    calls = seen.get("calls") or []
    if not params["linked"]:
        return [{"case": "no Express button when the company is not linked", "seen": seen,
                 "ok": seen.get("button") is False and not calls}]
    return [{"case": "the Express dialog opens", "seen": seen.get("dialog"), "ok": bool(seen.get("button")) and bool(seen.get("dialog"))},
            {"case": "the dialog asks by POST", "seen": calls, "ok": bool(calls) and all(method == "POST" for method in calls)}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("Purchase Document", {params['doc']!r}, ["ledger_state", "ledger_job"], as_dict=True), default=str))
""")
    return [{"case": "looking does not change the Express state", "saved": row,
             "ok": (row.get("ledger_state") or "") == params["state"] and (row.get("ledger_job") or "") == params["job"]}]
