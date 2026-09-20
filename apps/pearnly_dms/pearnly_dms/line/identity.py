"""Current operator identity for queued LINE work; cached job arguments grant no rights."""
import frappe
from pearnly_core import line_binding


def current(line_user_id):
    row = line_binding.binding_of(line_user_id)
    if not row:
        return None
    status = frappe.db.get_value("DMS Operator", {"user": row.user}, "status")
    if status is not None and status != "active":
        return None
    if status is None and not ({"DMS Admin", "System Manager"} & set(frappe.get_roles(row.user))):
        return None
    return {"user": row.user, "binding_revision": str(row.modified)}


def matches(binding, line_user_id):
    live = current(line_user_id)
    return bool(live and live["user"] == binding.get("user_id")
                and (not binding.get("binding_revision")
                     or live["binding_revision"] == binding["binding_revision"]))


def binding_changed(line_user_id):
    # Revoke old drafts, but never erase evidence of an unresolved external write.
    # This locking read participates in core's binding transaction (no commit here).
    from pearnly_core import line_session

    payload = (line_session._row(line_user_id, for_update=True) or {}).get("payload") or {}
    if payload.get("booking_attempt"):
        return
    frappe.db.delete("Pearnly LINE Session", {"line_user_id": line_user_id})
