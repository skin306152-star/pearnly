"""Local Desk adapter. Money is authoritative in the private cloud billing Site."""
import requests

import frappe
from frappe import _


def enabled():
    return bool(frappe.conf.get("pearnly_billing_remote_url"))


def call(action, **payload):
    url = str(frappe.conf.get("pearnly_billing_remote_url") or "").rstrip("/")
    token = frappe.conf.get("pearnly_billing_management_token")
    if not url or not token:
        frappe.throw(_("Billing service unavailable; please retry"))
    try:
        response = requests.post(
            url + "/api/method/pearnly_control.billing_control.dispatch",
            json={"action": action, "payload": payload},
            headers={"X-Pearnly-Billing-Management": token, "Host": "billing.internal"},
            timeout=20, allow_redirects=False,
        )
        response.raise_for_status()
        return response.json()["message"]
    except (requests.RequestException, ValueError, KeyError):
        # Never fall back to writing local balances when the cloud is unavailable.
        frappe.throw(_("Billing service unavailable; please retry"))


def provision_scope(tenant, product, company_name):
    return call("register", tenant=tenant, product=product, company_name=company_name)


def review(action, request, actor, amount=None, note=""):
    result = call(action, request=request, actor=actor, amount=amount, note=note)
    sync_topups(name=request)
    return result


def sync_topups(name=None):
    """Native Desk reads local display copies, never a second set of money records."""
    for item in topups(name=name):
        values = {key: item.get(key) for key in (
            "tenant", "status", "requested_thb", "approved_thb", "payer_name", "note",
            "review_note", "requested_at", "reviewed_at", "reviewed_by",
        )}
        if not frappe.db.exists("Pearnly Tenant", values["tenant"]):
            # A foreign control plane must not import tenants into this one.
            continue
        if frappe.db.exists("Pearnly Topup Request", item["name"]):
            frappe.db.set_value("Pearnly Topup Request", item["name"], values, update_modified=False)
        else:
            doc = frappe.get_doc({"doctype": "Pearnly Topup Request", **values})
            doc.flags.billing_remote_sync = True
            doc.insert(ignore_permissions=True, set_name=item["name"])
        frappe.db.set_value("Pearnly Topup Request", item["name"], {
            "creation": item["creation"], "modified": item["modified"],
        }, update_modified=False)
    frappe.db.commit()


def topups(name=None, pending=False):
    after = None
    while True:
        items = call("topups", name=name, after=after, pending=pending).get("items", [])
        yield from items
        if name or len(items) < 500:
            break
        after = items[-1]["name"]


def before_request():
    if not enabled():
        return
    request = getattr(frappe.local, "request", None)
    if not request:
        return
    command = request.path.removeprefix("/api/method/")
    command = frappe.form_dict.get("cmd") or command
    if command not in {"frappe.desk.reportview.get", "frappe.desk.form.load.getdoc"}:
        return
    if frappe.form_dict.get("doctype") != "Pearnly Topup Request":
        return
    from pearnly_control.billing_api import _require_admin
    _require_admin()
    sync_topups(name=frappe.form_dict.get("name"))
