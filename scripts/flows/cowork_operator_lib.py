"""Cowork 代录相关流程的共用造数据（`cowork_operator_unknown.py`、`cowork_pending_operator.py`）：在站点里用产品自己的函数
建一条 Express 连接（账套目录由实验室合成上报，同 `scripts/cowork-push-application-lab-verify.py`）、登记账套、从目录建一家
公司，再按场景建识别单据并走推送账本（准备 → 激活 → 电脑领单 → 领单超时 / 回执）。不连 Express、不发 LINE、不调模型。

数据都挂在标签为 `Flow operator Express` 的连接、账套键 `flowop` 下：`CLEANUP` 按连接删掉这条连接及其单据、推送记录、锁、
记住的选择与公司（每次 prepare 先清、verify 末尾再清），同一站点跑多次互不影响，跑完站点回到流程前的样子。
"""
from __future__ import annotations

LABEL = "Flow operator Express"
TAX = "0994000777001"  # 合成公司的税号（实验室专用）
SELLER_TAX = "0107561000013"
SUBJECT = f"purchase|{SELLER_TAX}"

# 以下片段交给 lab.python 在站点里执行（同一段 body 里按顺序拼接）

CLEANUP = f'''
from pearnly_cowork.companies import service as companies
def cleanup():
    frappe.set_user("admin@cowork.regression")
    linked = ["Cowork Companion Delivery", "Cowork Companion Request", "Cowork ERP Refresh", "Cowork ERP Snapshot",
              "Cowork Express Account Facts", "Cowork Express Account Set", "Cowork Express Agent Credential", "Cowork Express Choice",
              "Cowork Express Choice Change", "Cowork Express Reach", "Cowork Express Readback", "Cowork Folder Intake File",
              "Cowork Folder Intake Run", "Cowork Push Attempt", "Cowork Push Notification", "Cowork Push Writer Lock",
              "Cowork Push Record", "Cowork Tax Draft"]
    gone = {{"connections": 0, "documents": 0, "companies": 0}}
    for c in frappe.get_all("Cowork ERP Connection", filters={{"label": {LABEL!r}}}, pluck="name", limit_page_length=20):
        for company in frappe.get_all("Cowork Company", filters={{"seed_connection": c}}, pluck="name", limit_page_length=20):
            companies.delete(company)
            frappe.db.delete("Cowork Company Choice", {{"company": company}})
            gone["companies"] += 1
        hist = frappe.get_all("Cowork OCR History", filters={{"push_connection": c}}, pluck="name", limit_page_length=0)
        if hist:
            frappe.db.delete("Cowork OCR Correction", {{"source_history": ["in", hist]}})
            frappe.db.delete("Cowork OCR History", {{"name": ["in", hist]}})
        records = frappe.get_all("Cowork Push Record", filters={{"connection": c}}, pluck="name", limit_page_length=0)
        if records:
            frappe.db.delete("Cowork Push Writer Lock", {{"holder": ["in", records]}})
        for dt in linked:
            frappe.db.delete(dt, {{"connection": c}})
        frappe.db.delete("Cowork ERP Connection", {{"name": c}})
        gone["connections"] += 1
        gone["documents"] += len(hist)
    # 账套锁不带连接（按账套键）：结果不明的记录保留着锁，流程中途失败时也要放掉
    frappe.db.delete("Cowork Push Writer Lock", {{"account_key": "flowop"}})
    frappe.db.commit()
    return gone
def leftovers():
    return {{"connections": frappe.db.count("Cowork ERP Connection", {{"label": {LABEL!r}}}),
            "companies": frappe.db.count("Cowork Company", {{"tax_id": {TAX!r}}}),
            "locks": frappe.db.count("Cowork Push Writer Lock", {{"account_key": "flowop"}})}}
'''

BASE = f'''
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pearnly_cowork.access import scope
from pearnly_cowork.connections import pool, service as connections
from pearnly_cowork.history import storage
from pearnly_cowork.push.application import service as application
from pearnly_cowork.push.native import service as native
from pearnly_cowork.push.native.contracts import ReservationContext
frappe.set_user("admin@cowork.regression")
frappe.conf.pearnly_cowork_erp_push_enabled = True  # 只在这个造数据进程里开（推送账本要它），站点配置不动
cleanup()
ws = scope.existing_workspace()
conf = {{"account_set": "FLOWOP", "account_dir": "FLOWOP", "express_root": "D:\\\\FLOWOP", "fallback_acc": "A", "vat_input_acc": "V",
        "ap_acc": "P", "revenue_acc": "S", "vat_output_acc": "O", "ar_acc": "R"}}
conn = connections.configure(ws, "express", uuid4().hex, {LABEL!r}, conf, users=["admin@cowork.regression"])
refresh = connections.request_refresh(conn["name"], uuid4().hex)
observation = {{"adapter": "express", "observed_at": "2026-10-01T00:00:00Z", "collector": {{"kind": "synthetic_lab"}},
    "account_sets": [{{"source_id": "FLOWOP", "label": "Flow operator 69", "attributes": {{"root": "D:\\\\FLOWOP", "account_dir": "FLOWOP",
        "company": "Flow Operator Co", "tax_id": {TAX!r}, "branch": "00000", "period_start": "2026-01-01", "period_end": "2026-12-31",
        "writable": True, "mapping": {{k: conf[k] for k in ("fallback_acc", "vat_input_acc", "ap_acc", "revenue_acc", "vat_output_acc", "ar_acc")}}}}}}]}}
connections.install_snapshot(conn["name"], observation, refresh["request_id"], conn["revision"],
    {{"complete": True, "account_set": "FLOWOP", "root_key": "D:\\\\FLOWOP", "mappings": {{"accounts": [], "clients": []}},
     "supplier_profiles": {{}}, "bank_index": [], "reported_config": {{}}}})
pool.ensure_registered(frappe.get_doc("Cowork ERP Connection", conn["name"]), "flowop", root="D:\\\\FLOWOP", now=frappe.utils.now_datetime())
company = companies.create(conn["name"], "FLOWOP")
frappe.db.commit()
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
writer = native.trusted_adapter_context(conn["name"], "synthetic-writer")  # 实验室的合成写入端（同 cowork-push-native-lab-verify）
def context_of(record):
    row = frappe.get_doc("Cowork Push Record", record)
    return ReservationContext(row.actor, row.workspace, row.client or "", row.history, int(row.history_version), row.connection,
        row.account_key, branch=row.branch, binding_generation=int(row.binding_generation),
        connection_revision=int(row.connection_revision))
def recognized(number):
    fields = {{"direction": "purchase", "seller_name": "Flow seller", "seller_tax": {SELLER_TAX!r}, "buyer_tax": {TAX!r},
              "buyer_name": "Flow Operator Co", "subtotal": "100", "vat": "7", "total_amount": "107", "date": period + "-15",
              "document_type": "tax_invoice", "invoice_number": number}}
    pages = [{{"page_index": 1, "fields": fields}}]
    made = storage.create_recognized(ws, pages, uuid4().hex, source="manual", confidence="high", ai_raw=pages)
    storage.set_push_targets([made["name"]], conn["name"], "FLOWOP", "express")
    # 付款方式、费用科目记在账套记忆里（同复核页选过一次），推送准备才不停下等人选
    application.save_choices(made["name"], conn["name"], "FLOWOP", json.dumps([
        {{"kind": "payment", "subject": {SUBJECT!r}, "value": "credit", "label": "Flow seller"}},
        {{"kind": "account", "subject": {SUBJECT!r}, "value": "5300", "label": "Flow seller"}}]))
    frappe.db.commit()
    return made["name"]
def prepared(name, operator=None):
    doc = storage.read(name)
    result = application.prepare(name, conn["name"], uuid4().hex, doc.record_version, connections.detail(conn["name"])["revision"],
                                 "FLOWOP", operator=operator)
    if result.get("status") != "prepared":
        raise RuntimeError("not prepared: " + json.dumps(result, default=str)[:300])
    native.activate(result["name"], context=context_of(result["name"]), adapter=writer)  # 交出去：推送状态在途
    frappe.db.commit()
    return result["name"]
'''
