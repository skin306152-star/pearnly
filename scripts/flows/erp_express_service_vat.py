"""流程用例：ERP 采购单被电脑以「服务品进项税口径」拦下（CC 桌面版回 SERVICE_VAT_BASIS_CHOICE：赊购单有服务品，这本账套的历史
定不了这家供应商按 เกณฑ์สิทธิ 还是 เกณฑ์เงินสด，10-29），在单据的 Express 弹窗里选一次。

- new（公司接了 Express）：开一张带服务行的赊购单提交，照电脑拦下的样子把它的写账任务转人工（原因带这个码）、单据停下；
  打开单据点「Express」→ 弹窗里有「服务品进项税」一项、两个选项；选 เกณฑ์เงินสด 存 → 库里这家公司记下这一项（按账套 × 供应商），
  拦下的那件撤掉、单据重新准备。
- old（公司没接 Express）：没有「Express」按钮（同 `erp_document_express`）。
"""
from __future__ import annotations

import json
import uuid

from desk_list import ACTION, IDLE
from erp_lib import void_documents

TITLE = "ERP 采购单服务品进项税口径：电脑拦下后在 Express 弹窗选一次"
CODE = "SERVICE_VAT_BASIS_CHOICE"


def prepare(lab, manifest):
    linked = bool(manifest.get("express_linked"))
    if not linked:
        out = lab.python(manifest["site"], """
row = frappe.get_all("Purchase Document", filters={"status": "submitted"}, fields=["name"], order_by="creation asc",
                     limit_page_length=1)[0]
print(json.dumps(row, default=str))
""")
        return {"doc": out["name"], "linked": False}
    company = manifest["company"]
    out = lab.python(manifest["site"], f"""
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import contracts, push
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry, suppliers
frappe.set_user("purchase@erp.regression")
companies.set_active_company({company!r})
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายบริการทดสอบ"}}) or \\
    suppliers.create_supplier(supplier_name="ผู้ขายบริการทดสอบ", tax_id="0105536080112")["name"]
frappe.db.commit()
doc = entry.post({{"company": {company!r}, "supplier": supplier, "doc_date": "2026-10-20", "currency": "THB", "fx_rate": 1,
                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0, "is_paid": 0,
                  "doc_no": "FLOW-SRV-" + {uuid.uuid4().hex[:6]!r},
                  "lines": [{{"item_type": "service", "description": "ค่าขนส่ง", "qty": 1, "unit": "",
                             "unit_price": 300, "vat_rate": 7}}]}},
                 request_id="flow-service-vat-" + {uuid.uuid4().hex!r})
frappe.db.commit()
name = doc["name"]
row = frappe.db.get_value("Purchase Document", name, ["ledger_job", "ledger_book", "creation", "owner"], as_dict=True)
book = row.ledger_book or frappe.get_all("Pearnly CC Book", pluck="name", limit_page_length=1)[0]
job = row.ledger_job
if not job:
    job = jobs.enqueue(source_doctype="Purchase Document", source_name=name, request_key="flow-srv:" + name,
                       actor=row.owner, book_key=book, payload={{"direction": "purchase", "doctype": "RR"}}, label=name)["job"]
# 照电脑拦下的样子：任务转人工、原因带码，单据停下（`jobs.acknowledge` 收到要人处理的码就是这样落的）
frappe.db.set_value("Pearnly CC Job", job, {{"status": "manual", "stage": "needs_review",
                    "reason": "[{CODE}] service input VAT basis of supplier not decided"}}, update_modified=False)
frappe.db.set_value("Purchase Document", name, {{"ledger_state": contracts.HELD, "ledger_job": job, "ledger_book": book,
                    "ledger_reason": "[{CODE}] service input VAT basis of supplier not decided"}}, update_modified=False)
frappe.db.commit()
print(json.dumps({{"name": name, "job": job, "company": {company!r}}}, default=str))
""")
    return {"doc": out["name"], "job": out["job"], "company": out["company"], "linked": True}


def cleanup(lab, manifest, params):
    """自己开的那张采购单（上个月）：作废、撤掉没写的任务（成功、失败、超时都跑，`run.run_flow`）。"""
    if params["linked"]:
        void_documents(lab, manifest["site"], [params["doc"]])


def steps(params, camera_code=None):
    return [{"name": "Express 弹窗", "js": IDLE + ACTION + "await idle();"
             "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const via=await action('Express'); if(!via) return {button:false};"
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const d=cur_dialog; const f=d&&d.fields_dict&&Object.values(d.fields_dict).find(x=>x.df.fieldname.startsWith('pick_'));"
             "if(!f) return {button:true, dialog:(d&&d.$body.text()||'').replace(/\\s+/g,' ').trim().slice(0,300)};"
             "const options=(f.df.options||[]).filter(o=>o&&o.value).map(o=>({value:o.value,label:o.label}));"
             "const label=f.df.label; f.set_value('cash_basis'); await new Promise(r=>setTimeout(r,300));"
             "d.get_primary_btn().click(); await idle(); await new Promise(r=>setTimeout(r,500)); await idle();"
             "return {button:true, label, options};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "Express 弹窗"), None) or {}
    if not params["linked"]:
        return [{"case": "no Express button when the company is not linked", "seen": seen, "ok": seen.get("button") is False}]
    values = [option.get("value") for option in seen.get("options") or []]
    return [{"case": "the dialog asks for the service input VAT", "seen": seen.get("label"), "ok": bool(seen.get("label"))},
            {"case": "the two choices are accrual and cash basis", "seen": seen.get("options"),
             "ok": values == ["accrual", "cash_basis"] and all(option.get("label") not in ("accrual", "cash_basis")
                                                               for option in seen.get("options") or [])}]


def verify(lab, manifest, params, camera_code=None):
    if not params["linked"]:
        return []
    row = lab.python(manifest["site"], f"""
choice = frappe.get_all("ERP Express Choice", filters={{"company": {params['company']!r}, "kind": "service_vat"}},
                        fields=["subject", "value"], order_by="modified desc", limit_page_length=1)
job = frappe.db.get_value("Pearnly CC Job", {params['job']!r}, "status")
doc = frappe.db.get_value("Purchase Document", {params['doc']!r}, ["ledger_state", "ledger_job"], as_dict=True)
print(json.dumps({{"choice": choice[0] if choice else None, "old_job": job, "doc": doc}}, default=str))
""")
    choice = row.get("choice") or {}
    return [{"case": "the choice is remembered by book and supplier", "saved": choice,
             "ok": choice.get("value") == "cash_basis" and "|purchase|" in str(choice.get("subject") or "")},
            {"case": "the refused job is withdrawn and the document is prepared again", "saved": row,
             "ok": row.get("old_job") == "cancelled" and (row.get("doc") or {}).get("ledger_job") != params["job"]}]
