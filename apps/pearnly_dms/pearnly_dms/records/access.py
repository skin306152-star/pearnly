"""Shared authorization and Site-local DMS scope for history reads and removal."""
import frappe
from frappe import _

from pearnly_core.shell import entitlements

LOG_SOURCE = """FROM `tabPearnly Push Log` l
    LEFT JOIN `tabPearnly ERP Endpoint` e ON e.name = l.endpoint"""
DMS_SCOPE = """l.trigger != 'line_mirror' AND (
    e.adapter = 'mrerp_dms' OR
    JSON_UNQUOTE(JSON_EXTRACT(
        CASE WHEN JSON_VALID(l.request_body) THEN l.request_body ELSE '{}' END,
        '$.adapter')) = 'mrerp_dms'
)"""


def require_access():
    if (frappe.session.user == "Guest" or frappe.conf.get("pearnly_product") != "dms"
            or "push_record" not in entitlements()
            or not {"DMS Admin", "System Manager"}.intersection(frappe.get_roles())):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
