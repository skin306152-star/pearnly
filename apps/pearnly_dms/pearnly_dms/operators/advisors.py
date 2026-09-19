"""Roster commission assignments, resolved against the current DMS master list.

The old product resolved the name server-side from an id and refused ids that were not in
the roster read *at that moment*: the name printed on the month-end commission sheet must
never come from the caller. Same rule here, read through the existing masters cache.
"""
import logging

import frappe
from frappe import _
from pearnly_dms.masters import masters_cache

logger = logging.getLogger(__name__)


def read_options():
    """`(items, error_code)`. Read failures are returned, never raised.

    Raising here would turn "the master list is momentarily unreadable" into a failed
    dialog; the caller keeps the operator's stored assignment and skips the field.
    """
    endpoint = frappe.db.get_value("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "enabled": 1}, "name")
    if not endpoint:
        # Field-level wording: the create form separately refuses a missing connection.
        return [], "roster.advisor.noConnection"
    try:
        masters = masters_cache.get_masters(endpoint, force_refresh=True, require_complete=True)
    except Exception:
        logger.warning("[dms roster] advisor list read failed", exc_info=True)
        return [], "roster.advisor.loadError"
    if not masters:
        # The cache soft-falls back to `{}` when the DMS login fails: report a failed
        # read, never an empty roster.
        return [], "roster.advisor.loadError"
    return [{"id": str(row[0]), "name": str(row[2] or ""), "code": str(row[1] or "")}
            for row in masters.get("advisors", []) if len(row) > 2 and row[0]], ""


def options():
    """Options for callers that must succeed; raises the code instead."""
    items, error = read_options()
    if error:
        frappe.throw(_(error))
    return items


def resolve(advisor_id):
    advisor_id = str(advisor_id or "").strip()
    if not advisor_id:
        return {"id": "", "name": ""}
    selected = next((row for row in options() if row["id"] == advisor_id), None)
    if selected is None:
        frappe.throw(_("roster.advisor.invalid"))
    return selected
