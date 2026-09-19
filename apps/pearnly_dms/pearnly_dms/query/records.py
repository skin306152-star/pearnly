"""Read-only DMS ledger projection for the company's product administrators."""
import re

import frappe
from frappe import _
from frappe.utils import getdate

from pearnly_dms.records.access import DMS_SCOPE, LOG_SOURCE, require_access

STATUSES = ("pending", "retrying", "success", "failed", "skipped_dup", "manual")


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
        l.actor, l.attempt, l.error_msg
        {LOG_SOURCE} WHERE {where}
        ORDER BY l.creation DESC, l.name DESC LIMIT %(limit)s OFFSET %(offset)s""",
        values, as_dict=True)
    for row in rows:
        error = str(row.pop("error_msg", "") or "").strip()
        # Only stable codes are public; raw external errors can contain private payloads.
        row["error_code"] = error if re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", error) else ""
        row["has_error_detail"] = bool(error and not row["error_code"])
    return {"items": rows, "start": start, "page_length": page_length, "total": total}
