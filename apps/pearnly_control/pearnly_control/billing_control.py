"""Private billing-management RPC, accessible only through the operator tunnel.

Tenant credentials are deliberately not accepted. No generic method dispatch,
DocType writes, passwords, provisioning credentials or Desk are exposed here.
"""
import secrets

import frappe
from frappe import _

from pearnly_control import billing, billing_api
from pearnly_control.billing_auth import PRODUCTS, tenant_token

SERVICE_METHODS = frozenset({
    "service_status", "service_charge", "service_wallet", "service_topup_request",
    "service_topup_status", "service_home", "service_subscribe", "service_records",
})


def guard_private_site():
    if not frappe.conf.get("pearnly_billing_only"):
        return
    request = getattr(frappe.local, "request", None)
    if not request:
        return
    allowed = {f"/api/method/pearnly_control.billing_api.{name}" for name in SERVICE_METHODS}
    allowed.add("/api/method/pearnly_control.billing_control.dispatch")
    # No legacy cmd, alternate REST route, login, generic DocType API or Desk.
    if request.method != "POST" or request.path not in allowed or frappe.form_dict.get("cmd"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def _authorize_management():
    if not frappe.conf.get("pearnly_billing_only"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    expected = frappe.conf.get("pearnly_billing_management_token") or ""
    actual = frappe.get_request_header("X-Pearnly-Billing-Management") or ""
    if not expected or not actual or not secrets.compare_digest(expected, actual):
        frappe.throw(_("Invalid service token"), frappe.AuthenticationError)


def _reviewer(payload):
    actor = payload.get("actor") or ""
    if actor not in (frappe.conf.get("pearnly_billing_reviewers") or []):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not frappe.db.exists("User", actor):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return actor


@frappe.whitelist(allow_guest=True, methods=["POST"])
def dispatch(action: str, payload=None):
    _authorize_management()
    payload = frappe.parse_json(payload) or {}
    if not isinstance(payload, dict):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if action == "register":
        tenant = str(payload.get("tenant") or "").strip()
        product = payload.get("product")
        if not tenant or len(tenant) > 140 or product not in PRODUCTS:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        if not frappe.db.exists("Pearnly Tenant", tenant):
            frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": payload.get("company_name") or tenant,
                            "status": "active"}).insert(ignore_permissions=True, set_name=tenant)
        billing.wallet_of(tenant)
        frappe.db.commit()
        return {"token": tenant_token(billing_api.service_token(create=False), tenant, product)}
    if action == "slip":
        from pearnly_control.payments.topup import evidence
        return evidence(payload["request"])
    if action == "topups":
        filters = {"name": payload["name"]} if payload.get("name") else {}
        if payload.get("after") and not payload.get("name"):
            filters["name"] = (">", str(payload["after"]))
        if payload.get("pending"):
            filters["status"] = "pending"
        return {"items": frappe.get_all(billing.TOPUP, filters=filters,
            fields=["name", "tenant", "status", "requested_thb", "approved_thb", "payer_name",
                    "note", "review_note", "requested_at", "reviewed_at", "reviewed_by",
                    "creation", "modified"], order_by="name asc", limit_page_length=500)}
    if action == "purge":
        tenant = str(payload.get("tenant") or "").strip()
        if not tenant:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        for doctype in ("Pearnly Credit Transaction", billing.TOPUP, billing.SUBSCRIPTION, billing.WALLET):
            frappe.db.delete(doctype, {"tenant": tenant})
        if frappe.db.exists("Pearnly Tenant", tenant):
            frappe.delete_doc("Pearnly Tenant", tenant, ignore_permissions=True, force=True, delete_permanently=True)
        frappe.db.commit()
        return {"purged": True}
    if action == "summary":
        return billing.wallet_summary(billing_api._tenant_or_throw(payload.get("tenant")))
    if action == "approve":
        return billing_api.approve_local(payload["request"], _reviewer(payload),
                                         payload.get("amount"), payload.get("note") or "")
    if action == "reject":
        return billing_api.reject_local(payload["request"], _reviewer(payload), payload.get("note") or "")
    if action == "adjust":
        actor = _reviewer(payload)
        tenant = billing_api._tenant_or_throw(payload.get("tenant"))
        balance = billing.adjust(tenant, payload.get("amount"), note=payload.get("note") or "", actor=actor)
        return {"ok": True, "tenant": tenant, "new_balance": float(balance)}
    frappe.throw(_("Not permitted"), frappe.PermissionError)
