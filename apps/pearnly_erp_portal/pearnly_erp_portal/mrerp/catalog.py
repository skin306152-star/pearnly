"""MR.ERP 账套目录与 ERP 公司选账本（评估 §14.6）：管理员在服务端刷新这条连接能进的账套（后台任务，网络那一半是共用层
`pearnly_core.mrerp.collect`，与 Cowork 采集同一份），给某家 ERP 公司选一份当账本。

一家公司只有一个账本：选了 MR.ERP 就不能同时勾着「接 Express」（先在公司上取消 Express）；选账本时记「接上时刻」
（``mrerp_linked_at``），之后开始的采购 / 销售单才导入（同 Express「接上之后开始的单」口径，`express.push.counts_from`）。
目录是连接上的一份快照（``catalog_json``，`pearnly_core.mrerp.catalog.account_sets` 的形状），列账套在服务端按页切。
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.mrerp import catalog as mrerp_catalog

from pearnly_erp_portal.mrerp import connection as connections

COMPANY = "ERP Company"
JOB_TIMEOUT_SECONDS = 240
PAGE_MAX = 100


def refresh(name):
    """申请重读这条连接的账套目录（只限管理员；同一连接只排一件，提交后可见）。"""
    connections.require_admin()
    if not frappe.db.exists(connections.CONNECTION, name):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frappe.enqueue("pearnly_erp_portal.mrerp.catalog.run", queue="short", job_id=f"erp-mrerp-catalog::{name}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, connection=name)
    return {"connection": name, "requested_at": str(now_datetime())}


def run(connection):
    """（后台任务）登录、列账套、补期间与税号（不持行锁）→ 存目录快照或错误码。永不抛到队列。"""
    if not connections.mrerp.enabled():
        return
    from pearnly_core.mrerp import collect as mrerp_collect  # 连网的共用层只在采集任务里载入（同 `push.run`）

    lease = connections.lease(connection)
    frappe.db.commit()
    if not lease:
        return
    result = mrerp_collect.collect(lease)
    if result.get("error_code"):
        frappe.db.set_value(connections.CONNECTION, connection, "catalog_error", str(result["error_code"])[:140],
                            update_modified=False)
    else:
        frappe.db.set_value(connections.CONNECTION, connection, {
            "catalog_json": json.dumps(mrerp_catalog.account_sets(result["rows"]), ensure_ascii=False),
            "catalog_at": now_datetime(), "catalog_error": ""}, update_modified=False)
    frappe.db.commit()


def _sets(name) -> list:
    try:
        rows = json.loads(frappe.db.get_value(connections.CONNECTION, name, "catalog_json") or "[]")
    except ValueError:
        rows = []
    return [row for row in rows if isinstance(row, dict) and row.get("source_id")]


def account_sets(name, start=0, page_length=20, search=None) -> dict:
    """这条连接的账套（只限管理员），服务端按页切：``{rows: [{key, label, company, period_start, period_end, tax_id}], total}``。"""
    connections.require_admin()
    text = str(search or "").strip().lower()
    rows = [{"key": row["source_id"], "label": row.get("label") or row["source_id"],
             **{k: v for k, v in (row.get("attributes") or {}).items()
                if k in ("company", "period_start", "period_end", "tax_id")}} for row in _sets(name)]
    if text:
        rows = [row for row in rows if text in f"{row['label']} {row.get('company') or ''}".lower()]
    start, size = max(int(start or 0), 0), min(max(int(page_length or 20), 1), PAGE_MAX)
    return {"rows": rows[start:start + size], "total": len(rows)}


def choose(company, connection, account_set):
    """这家公司的账本改成这条连接上的这份 MR.ERP 账套（只限管理员）。账套必须在目录里；公司还勾着 Express 就拒。"""
    connections.require_admin()
    row = frappe.db.get_value(COMPANY, company, ["name", "express_enabled", "mrerp_enabled", "mrerp_connection",
                                                 "mrerp_account_set"], as_dict=True)
    if not row or row.express_enabled:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    key = str(account_set or "").strip()
    if key not in {item["source_id"] for item in _sets(connection)}:
        frappe.throw(_("Invalid request"))
    values = {"mrerp_enabled": 1, "mrerp_connection": connection, "mrerp_account_set": key}
    if not row.mrerp_enabled or (row.mrerp_connection, row.mrerp_account_set) != (connection, key):
        values["mrerp_linked_at"] = now_datetime()  # 换了账套重记：之前开始的单不导入
    frappe.db.set_value(COMPANY, company, values)
    return values


def detach(company):
    """这家公司不再用 MR.ERP 当账本（只限管理员）：之后提交的单不导入；已导入的不动。"""
    connections.require_admin()
    frappe.db.set_value(COMPANY, company, {"mrerp_enabled": 0, "mrerp_linked_at": None})


__all__ = ["account_sets", "choose", "detach", "refresh", "run"]
