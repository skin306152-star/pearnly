"""Read-only DMS ledger projection for the company's product administrators."""
import re

import frappe
from frappe import _
from frappe.utils import getdate

from pearnly_dms.records.access import DMS_SCOPE, LOG_SOURCE, require_access

STATUSES = ("pending", "retrying", "success", "failed", "skipped_dup", "manual")
#: 客户档写入在台账正文里的两种标记：LINE / 网页经 `intake.customer_attempt` 写的
#: `operation=customer_write`，以及流程自己补记、审批执行记的 `mode`。
CUSTOMER_WRITE_MODES = frozenset({"create", "overwrite", "update"})


def _json_field(column, path):
    """台账正文里的一个标量（正文不是合法 JSON 时为空）：只取这一格，不把正文带出库。"""
    return (f"JSON_UNQUOTE(JSON_EXTRACT(CASE WHEN JSON_VALID({column}) THEN {column} "
            f"ELSE '{{}}' END, '{path}'))")


def _kind_and_number(row):
    """记录类型（`customer` 客户资料 / `booking` 订车）与给人看的编号。

    客户写入的 `business_key` 存的是 DMS **内部行号**（回读、恢复都靠它，不改），DMS 界面上
    看不到这个号；这里改显示写后回读到的 DMS 客户编号（`cuscode`），旧记录没有就留空。
    订车的 `business_key` 本来就是 DMS 订车单号。
    """
    request_mode = str(row.pop("request_mode", None) or "")
    operation = str(row.pop("request_operation", None) or "")
    cuscode = str(row.pop("cuscode", None) or "")
    if request_mode == "booking":
        return "booking", row.get("business_key") or ""
    if operation == "customer_write" or request_mode in CUSTOMER_WRITE_MODES:
        return "customer", cuscode
    return "", row.get("business_key") or ""


def _filters(status, from_date, to_date):
    conditions = [DMS_SCOPE, "l.records_deleted_at IS NULL"]
    values = {}
    if status:
        if status not in STATUSES:
            frappe.throw(_("Invalid value"))
        conditions.append("l.status = %(status)s")
        values["status"] = status
    if from_date:
        values["from_date"] = getdate(from_date)
        conditions.append("l.creation >= %(from_date)s")
    if to_date:
        values["to_date"] = getdate(to_date)
        conditions.append("l.creation < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)")
    if from_date and to_date and values["from_date"] > values["to_date"]:
        frappe.throw(_("Invalid value"))
    return " AND ".join(conditions), values


@frappe.whitelist(methods=["GET"])
def list_records(status="", from_date=None, to_date=None, start=0, page_length=20):
    require_access()
    try:
        start, page_length = int(start), int(page_length)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid value"))
    if start < 0 or start > 2500000 or not 1 <= page_length <= 2500:
        frappe.throw(_("Invalid value"))
    where, values = _filters(status, from_date, to_date)
    # Deliberate scoped read: no raw payloads or shared DocType grants to product users.
    total = frappe.db.sql(f"SELECT COUNT(*) {LOG_SOURCE} WHERE {where}", values)[0][0]
    values.update(limit=page_length, offset=start)
    rows = frappe.db.sql(f"""SELECT l.name, l.creation, l.status, l.business_key,
        l.actor, COALESCE(NULLIF(op.display_name, ''), NULLIF(u.full_name, ''), '') AS actor_name,
        l.attempt, l.error_msg,
        {_json_field("l.request_body", "$.mode")} AS request_mode,
        {_json_field("l.request_body", "$.operation")} AS request_operation,
        COALESCE({_json_field("l.response_body", "$.cuscode")},
                 {_json_field("l.response_body", "$.response_body.cuscode")}) AS cuscode
        {LOG_SOURCE}
        LEFT JOIN `tabDMS Operator` op ON op.user = l.actor
        LEFT JOIN `tabUser` u ON u.name = l.actor
        WHERE {where}
        ORDER BY l.creation DESC, l.name DESC LIMIT %(limit)s OFFSET %(offset)s""",
        values, as_dict=True)
    for row in rows:
        error = str(row.pop("error_msg", "") or "").strip()
        # Only stable codes are public; raw external errors can contain private payloads.
        row["error_code"] = error if re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", error) else ""
        row["has_error_detail"] = bool(error and not row["error_code"])
        row["kind"], row["number"] = _kind_and_number(row)
    return {"items": rows, "start": start, "page_length": page_length, "total": total}
