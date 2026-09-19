"""Remove selected records from product history without erasing business evidence."""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from .access import DMS_SCOPE, LOG_SOURCE, require_access


@frappe.whitelist(methods=["POST"])
def delete_records(names):
    require_access()
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except (ValueError, TypeError):
            frappe.throw(_("Invalid value"))
    if (not isinstance(names, list) or not 1 <= len(names) <= 2500
            or any(not isinstance(name, str) or not name.strip() or len(name) > 140
                   for name in names)):
        frappe.throw(_("Invalid value"))
    names = sorted(set(names))
    # Validate the entire selection before writing. Already-removed rows remain
    # in scope so retries are idempotent. Lock rows until the request commits.
    rows = frappe.db.sql(f"""SELECT l.name, l.records_deleted_at
        {LOG_SOURCE} WHERE {DMS_SCOPE} AND l.name IN %(names)s
        ORDER BY l.name FOR UPDATE""", {"names": names}, as_dict=True)
    if {row.name for row in rows} != set(names):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    pending = [row.name for row in rows if not row.records_deleted_at]
    if pending:
        # Only display/audit fields change. Status, leases, retries, customer
        # deduplication and business-document readback still use the original log.
        frappe.db.set_value("Pearnly Push Log", {"name": ["in", pending]}, {
            "records_deleted_at": now_datetime(),
            "records_deleted_by": frappe.session.user,
        }, update_modified=False)
    return {"deleted": len(pending)}
