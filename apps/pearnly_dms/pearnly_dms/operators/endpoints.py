"""Operator credentials -> owned DMS connections, within the current Site transaction.

The shared connection supplies organization configuration; the operator supplies the
sales login and commission identity. Endpoint history is retained when access ends.
"""
import json

import frappe
from frappe import _
from pearnly_dms.contracts.models import PERSONAL_BOOKING_DEFAULT_KEYS

ENDPOINT = "Pearnly ERP Endpoint"


def shared_connection():
    names = frappe.get_all(ENDPOINT, filters={"adapter": "mrerp_dms", "owner_user": ["is", "not set"]},
                           pluck="name", limit_page_length=2)
    if len(names) != 1:
        frappe.throw(_("roster.error.noConnection"))
    return frappe.get_doc(ENDPOINT, names[0])


def sync_operator(operator, method=None):
    """Runs on native document updates as well as product APIs. Never contacts DMS."""
    template = shared_connection()
    # Serialize creates/credential changes for the same identity across requests.
    frappe.db.sql("select name from `tabDMS Operator` where name=%s for update", operator.name)
    names = frappe.get_all(ENDPOINT, filters={"adapter": "mrerp_dms", "owner_user": operator.user},
                           pluck="name", limit_page_length=2)
    if len(names) > 1:
        frappe.throw(_("Multiple DMS connections exist for this operator"))
    endpoint = frappe.get_doc(ENDPOINT, names[0]) if names else frappe.new_doc(ENDPOINT)
    endpoint.adapter = "mrerp_dms"
    endpoint.owner_user = operator.user
    endpoint.label = operator.display_name
    endpoint.system_url = template.system_url
    endpoint.username = operator.dms_username
    password = operator.get_password("dms_password", raise_exception=False)
    if not endpoint.username or not password:
        frappe.throw(_("Fill in the display name, DMS username and password"))
    endpoint.password = password
    endpoint.admin_username = template.admin_username or ""
    endpoint.admin_password = template.get_password("admin_password", raise_exception=False) or ""
    defaults = frappe.parse_json(template.booking_defaults or "{}") or {}
    defaults = {key: value for key, value in defaults.items() if key not in PERSONAL_BOOKING_DEFAULT_KEYS}
    defaults.update(advisor_id=operator.advisor_id or "", advisor_name=operator.advisor_name or "")
    endpoint.booking_defaults = json.dumps(defaults, ensure_ascii=False)
    endpoint.enabled = int(bool(template.enabled) and operator.status == "active")
    endpoint.auto_push = 0
    endpoint.is_default = 0
    endpoint.save(ignore_permissions=True)
    return endpoint.name


def sync_shared_connection(doc, method=None):
    """Frappe hook: shared credential/URL/status updates reach all owned endpoints."""
    if doc.adapter != "mrerp_dms" or doc.owner_user:
        return
    for name in frappe.get_all("DMS Operator", pluck="name", order_by="name"):
        sync_operator(frappe.get_doc("DMS Operator", name))


def disable_operator(operator, method=None):
    for name in frappe.get_all(ENDPOINT, filters={"adapter": "mrerp_dms", "owner_user": operator.user}, pluck="name"):
        frappe.db.set_value(ENDPOINT, name, "enabled", 0)


def backfill():
    if not frappe.db.exists(ENDPOINT, {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}):
        return
    for name in frappe.get_all("DMS Operator", pluck="name", order_by="name"):
        sync_operator(frappe.get_doc("DMS Operator", name))
