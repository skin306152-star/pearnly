"""Cached roster choices; narrow live verification when saving an assignment.

Opening a dialog reads only the manually published company dataset. Saving a
nonempty advisor ID reads only the authoritative native advisor selector, never
the old full-master cache. Names always come from that verified live response.
"""
import logging

import frappe
from frappe import _
from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.snapshots import store

logger = logging.getLogger(__name__)
ADMIN_ROLES = frozenset({"DMS Admin", "System Manager"})
PRIMARY_FILTERS = {"adapter": "mrerp_dms", "enabled": 1, "owner_user": ["is", "not set"]}


def _primary_endpoint():
    names = frappe.get_all("Pearnly ERP Endpoint", filters=PRIMARY_FILTERS,
                           pluck="name", limit_page_length=2)
    return names[0] if len(names) == 1 else None


def _items(rows):
    """Reject malformed or ambiguous lists instead of silently dropping rows."""
    if not isinstance(rows, list):
        raise DmsClientError("Advisor directory unavailable", "ERR_DMS_MASTER_UNAVAILABLE")
    result, seen = [], set()
    for row in rows:
        if (not isinstance(row, (list, tuple)) or len(row) < 3
                or any(not isinstance(row[index], (str, int)) or isinstance(row[index], bool)
                       for index in (0, 2))
                or (row[1] is not None and (not isinstance(row[1], (str, int)) or isinstance(row[1], bool)))):
            raise DmsClientError("Advisor directory malformed", "ERR_DMS_MASTER_UNAVAILABLE")
        identity, code, name = str(row[0]).strip(), str(row[1] or "").strip(), str(row[2]).strip()
        if not identity or identity in seen or not name:
            raise DmsClientError("Advisor directory ambiguous", "ERR_DMS_MASTER_UNAVAILABLE")
        seen.add(identity)
        result.append({"id": identity, "name": name, "code": code})
    return result


def _admin(expected=None, *, current=False):
    """Recheck the same enabled administrator before returning a live result.

    Final locking reads see current committed permissions even if the caller
    began a repeatable-read transaction before the external read. These short
    local locks stay in the caller's transaction; this helper never commits it.
    """
    actor = frappe.session.user
    if not actor or actor == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if expected is not None and actor != expected:
        frappe.throw(_("Only an admin can manage the operator roster"), frappe.PermissionError)
    if current:
        users = frappe.db.sql("SELECT enabled FROM `tabUser` WHERE name=%s LOCK IN SHARE MODE", actor)
        roles = frappe.db.sql("SELECT role FROM `tabHas Role` WHERE parent=%s AND parenttype='User' "
                              "LOCK IN SHARE MODE", actor)
        enabled = bool(users and users[0][0])
        # Frappe grants Administrator every role without Has Role rows. Keep
        # that native rule, while still requiring the current User to be enabled.
        allowed = actor == "Administrator" or ADMIN_ROLES.intersection(row[0] for row in roles)
    else:
        enabled = frappe.db.get_value("User", actor, "enabled")
        allowed = ADMIN_ROLES.intersection(frappe.get_roles(actor))
    if not enabled or not allowed:
        frappe.throw(_("Only an admin can manage the operator roster"), frappe.PermissionError)
    return actor


def read_options():
    """`(items, error_code)`. Read failures are returned, never raised.

    Raising here would turn "the master list is momentarily unreadable" into a failed
    dialog; the caller keeps the operator's stored assignment and skips the field.
    """
    _admin()
    endpoint = _primary_endpoint()
    if not endpoint:
        # Field-level wording: the create form separately refuses a missing connection.
        return [], "roster.advisor.noConnection"
    try:
        snapshot = store.read(endpoint, "company")
        return _items(snapshot["data"]["masters"]["advisors"]), ""
    except Exception:
        logger.warning("[dms roster] cached advisor list unavailable")
        return [], "roster.advisor.loadError"


def options():
    """Options for callers that must succeed; raises the code instead."""
    items, error = read_options()
    if error:
        frappe.throw(_(error))
    return items


def resolve(advisor_id):
    """Validate only a requested assignment live; never trust a cached name."""
    actor = _admin()
    advisor_id = str(advisor_id or "").strip()
    if not advisor_id:
        return {"id": "", "name": ""}
    endpoint = _primary_endpoint()
    if not endpoint:
        frappe.throw(_("roster.advisor.noConnection"))
    try:
        expected_scope = store.scope(endpoint)
    except Exception:
        frappe.throw(_("roster.advisor.loadError"))

    def read(client, adapter):
        # The existing native selector pagination rejects truncation/failure;
        # no customer, vehicle, bank, address or employee master is read here.
        return {"ok": True, "items": _items(client._advisor_rows())}

    result = run_logged_in(endpoint, read, authoritative_read=True, catch_up=True)
    _admin(actor, current=True)
    try:
        with store._cache_connection() as database:
            names = database.sql("SELECT name FROM `tabPearnly ERP Endpoint` WHERE adapter='mrerp_dms' "
                "AND enabled=1 AND COALESCE(owner_user, '')='' LOCK IN SHARE MODE")
            if (len(names) != 1 or names[0][0] != endpoint
                    or store.scope(endpoint, current=True, _database=database) != expected_scope):
                raise ValueError("Advisor authority changed during read")
    except Exception:
        frappe.throw(_("roster.advisor.loadError"))
    if not isinstance(result, dict) or not result.get("ok"):
        frappe.throw(_("roster.advisor.loadError"))
    selected = next((row for row in result["items"] if row["id"] == advisor_id), None)
    if selected is None:
        frappe.throw(_("roster.advisor.invalid"))
    return selected
