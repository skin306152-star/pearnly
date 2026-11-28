"""MR.ERP 结果不明「核对」出口（用户 11-28 批准）真页面流程的共用备数：一家接编造 MR.ERP 连接的公司（同 `erp_cc_guide_mrerp_lib`
的连接与建公司写法，公司设置经 lab.set_fields 改、格结束还原），这家公司下用正式入账函数开采购 / 销售单（入账时不排写账：备数进程里
把 push.queue 换成空的），再把单子记成「写进 MR.ERP 结果不明」（导入发出后报错的落库样子 `mrerp.contracts.settle` status=unknown）。
收款：同一家公司一笔收款记录、状态失败 + 结果不明原因（导入发出后报错的落库样子 `collection.mrerp.settle`）。不连真 MR.ERP、不带凭据。"""
from __future__ import annotations

import json
import uuid

import erp_cc_guide_mrerp_lib as mrerp_lib
from erp_lib import void_documents

USER = "admin@erp.regression"
UNKNOWN = "mrerp.result_unknown: ERR_TECHNICAL"


def prepare(lab, manifest, *, purchases=0, sales=0, collections=0) -> dict:
    tag = "muc" + uuid.uuid4().hex[:6]
    site = manifest["site"]
    made = lab.python(site, f"TAG, DIRECTION = {tag!r}, 'purchase'\n" + mrerp_lib.SEED)
    lab.set_fields(site, "ERP Company", made["company"], {
        "mrerp_enabled": 1, "express_enabled": 0, "mrerp_connection": made["connection"], "mrerp_account_set": mrerp_lib.SET,
        "mrerp_company": "tax:" + mrerp_lib.BOOK_TAX, "mrerp_linked_at": mrerp_lib._five_minutes_ago(lab, site)})
    out = lab.python(site, f"""
from pearnly_erp_portal.express import push
from pearnly_erp_portal.masters import company as companies, customers
from pearnly_erp_portal.purchasing import entry as purchase, suppliers
from pearnly_erp_portal.sales import entry as sales
co, tag = {made['company']!r}, {tag!r}
frappe.set_user({USER!r})
companies.set_active_company(co)
queue, push.queue = push.queue, lambda doctype, name: None  # 备数入账不排写账（只在这个进程里）
docs = {{"purchase": [], "sales": []}}
try:
    supplier = suppliers.create_supplier(supplier_name="ผู้ขาย " + tag)["name"]
    customer = customers.create_customer(customer_name="ลูกค้า " + tag)["name"]
    line = {{"item_type": "service", "description": "ค่าบริการ " + tag, "qty": 1, "unit": "", "unit_price": 100, "vat_rate": 7}}
    for n in range({purchases}):
        docs["purchase"].append(purchase.post({{"company": co, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB",
            "fx_rate": 1, "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0, "is_paid": 0,
            "doc_no": tag.upper() + "-P" + str(n), "lines": [line]}}, request_id="flow-muc-p" + str(n) + "-" + tag)["name"])
    for n in range({sales}):
        docs["sales"].append(sales.post({{"company": co, "customer": customer, "doc_date": frappe.utils.today(), "has_vat": 1, "vat_rate": 7,
            "source": "manual", "doc_kind": "receipt", "paid_amount": 107, "payment_method": "cash",
            "payment_date": frappe.utils.today(), "lines": [line]}}, request_id="flow-muc-s" + str(n) + "-" + tag)["name"])
finally:
    push.queue = queue
for doctype, names in (("Purchase Document", docs["purchase"]), ("ERP Sales Document", docs["sales"])):
    for name in names:
        frappe.db.set_value(doctype, name, {{"ledger_state": "held", "ledger_reason": {UNKNOWN!r}, "ledger_target": "mrerp",
                                             "ledger_needed": None}}, update_modified=False)
cols = []
for n in range({collections}):
    doc = frappe.get_doc({{"doctype": "ERP Collection", "name": "COL" + tag.upper() + str(n), "company": co, "customer": customer, "customer_name": "ลูกค้า " + tag,
        "doc_date": frappe.utils.today(), "collector": {USER!r}, "source": "web", "status": "failed", "reason": {UNKNOWN!r},
        "cash": 107, "total": 107, "book_key": {mrerp_lib.SET!r}, "book_label": "FLOWBOOK",
        "invoices": [{{"docnum": tag.upper() + "-IV" + str(n), "amount": 107, "remaining": 107}}]}}).insert(ignore_permissions=True)
    cols.append(doc.name)
frappe.db.commit()
print(json.dumps({{"company": co, "connection": {made['connection']!r}, "purchase": docs["purchase"], "sales": docs["sales"],
                  "collections": cols}}, default=str))
""")
    return {**out, "tag": tag, "business": "purchase", "cases": []}


def states(lab, manifest, params) -> dict:
    return lab.python(manifest["site"], f"""
out = {{}}
for doctype, names in (("Purchase Document", {params.get('purchase') or []!r}), ("ERP Sales Document", {params.get('sales') or []!r})):
    for name in names:
        out[name] = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_docnum", "ledger_reason"], as_dict=True)
for name in {params.get('collections') or []!r}:
    out[name] = frappe.db.get_value("ERP Collection", name, ["status", "docnum", "reason"], as_dict=True)
out["comments"] = frappe.get_all("Comment", filters={{"reference_name": ["in", list(out) or [""]], "comment_type": "Info",
                                                      "content": ["like", "MR.ERP check by%"]}}, pluck="reference_name")
print(json.dumps(out, default=str))
""")


def cleanup(lab, manifest, params):
    site = manifest["site"]
    if params.get("purchase"):
        void_documents(lab, site, params["purchase"])
    if params.get("sales"):
        void_documents(lab, site, params["sales"], doctype="ERP Sales Document")
    lab.python(site, f"""
frappe.set_user("Administrator")
for name in {params.get('collections') or []!r}:
    frappe.db.delete("ERP Collection Invoice", {{"parent": name}})
    frappe.db.delete("ERP Collection", {{"name": name}})
frappe.db.commit()
print("{{}}")
""")
    mrerp_lib.cleanup(lab, manifest, {"tag": params.get("tag") or "", "business": "purchase"})
