"""Cowork 流程共用片段：一家接 MR.ERP 的公司（编造的 MR.ERP 连接、账套目录与导入菜单，不带登录凭据）。

造法同 `cowork_mrerp_login.prepare`（产品自己的函数，不连 MR.ERP）：``connections.configure`` 建 MR.ERP 连接 → 装一份合成账套目录
（一本覆盖今天的年度账套，账套键 :data:`SET`）→ ``companies.create`` 从目录建公司。没有凭据，推送的后台任务停在
「登录不了 MR.ERP」，流程里不去真 MR.ERP。

``SEED`` 在站点里跑完留下 ``mr_conn``（连接号）与 ``mr_company``（公司号），``mr_cleanup()`` 删掉这家公司、连接与本连接的票。
"""
from __future__ import annotations

USER = "admin@cowork.regression"
SET = "88:1"
BOOK_TAX = "0105561000038"   # 编造（过校验位）
LABEL = "Flow MR.ERP book"

CLEANUP = f'''
def mr_cleanup():
    from pearnly_cowork.companies import service as mr_companies
    frappe.set_user({USER!r})
    gone = 0
    for c in frappe.get_all("Cowork ERP Connection", filters={{"adapter": "mrerp", "label": {LABEL!r}}}, pluck="name",
                            limit_page_length=20):
        for company in frappe.get_all("Cowork Company", filters={{"seed_connection": c}}, pluck="name", limit_page_length=20):
            mr_companies.delete(company)
            frappe.db.delete("Cowork Company Choice", {{"company": company}})
        left = frappe.get_all("Cowork OCR History", filters={{"push_connection": c}}, pluck="name", limit_page_length=0)
        if left:
            frappe.db.delete("Cowork OCR Correction", {{"source_history": ["in", left]}})
            frappe.db.delete("Cowork Push Record", {{"history": ["in", left]}})
            frappe.db.delete("Cowork OCR History", {{"name": ["in", left]}})
        frappe.db.delete("Cowork Push Record", {{"connection": c}})
        for dt in ("Cowork ERP Refresh", "Cowork ERP Snapshot"):
            frappe.db.delete(dt, {{"connection": c}})
        frappe.db.delete("Cowork ERP Connection", {{"name": c}})
        gone += 1
    frappe.db.commit()
    return gone
'''

SEED = CLEANUP + f'''
from uuid import uuid4 as _uuid4
from pearnly_cowork.access import scope as mr_scope
from pearnly_cowork.companies import service as mr_companies
from pearnly_cowork.connections import service as mr_connections
mr_cleanup()
frappe.set_user({USER!r})
_ws = mr_scope.existing_workspace()
_conn = mr_connections.configure(_ws, "mrerp", _uuid4().hex, {LABEL!r}, {{}}, users=[{USER!r}])
_refresh = mr_connections.request_refresh(_conn["name"], _uuid4().hex)
mr_connections.install_snapshot(_conn["name"], {{"adapter": "mrerp", "observed_at": "2026-10-01T00:00:00Z",
    "collector": {{"kind": "synthetic_lab"}},
    "account_sets": [{{"source_id": {SET!r}, "label": "FLOWBOOK", "attributes": {{"company": "FLOWCO",
        "company_name": "บริษัท โฟลว์ จำกัด", "comidyear": {SET.split(":")[0]!r}, "seldb": "1", "period_start": "2023-01-01",
        "period_end": "2027-12-31", "tax_id": {BOOK_TAX!r}}}}}]}}, _refresh["request_id"], _conn["revision"], None)
mr_company = mr_companies.create(_conn["name"], {SET!r})["id"]
mr_conn = _conn["name"]
frappe.db.commit()
'''
