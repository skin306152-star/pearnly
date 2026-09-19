"""Formal accounts belong to cloud tenants, regardless of console location.

Runtime executor configuration is operator-owned. Never infer a local tenant from
an absent setting, and never move an existing request to a different target.
Loopback API URLs are valid SSH tunnels to cloud identity/billing services.
"""

import frappe
from frappe import _

from pearnly_control import infra


def cloud_target_names() -> list[str]:
    configured = frappe.conf.get("pearnly_target_runtime") or {}
    return [name for name, settings in configured.items()
            if isinstance(settings, dict) and settings.get("executor") == "production"]


def require_cloud_target(target: str | None = None, *, use_default: bool = True) -> str:
    name = target
    if not name and use_default:
        name = frappe.db.get_value("Pearnly Target", {"is_default": 1}, "name")
    if not name or not frappe.db.exists("Pearnly Target", name) or not infra.is_cloud(name):
        frappe.throw(_("Account opening is available only for configured cloud targets"))
    return name


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search(doctype, txt, searchfield, start, page_len, filters):
    from pearnly_control.issues import _require_operator

    _require_operator()
    if doctype != "Pearnly Target":
        return []
    names = cloud_target_names()
    if not names:
        return []
    return frappe.get_list(
        "Pearnly Target",
        filters={"name": ["in", names], "target_name": ["like", f"%{txt}%"]},
        fields=["name", "target_name"],
        order_by="is_default desc, target_name asc",
        start=start, page_length=page_len, as_list=True,
    )
