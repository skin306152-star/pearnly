"""Read-only permission probe for the ERP/POS laboratory.

Documents the *current* permission behaviour of the ERP portal before any
permission work: create a synthetic purchase document as an owner user, then ask
what a cashier-like user (no business roles) can actually read through the
whitelisted API. Writes only labelled synthetic data into the lab volume.

Does not contact identity, LINE, ERP or billing services.
"""
import json
import os
from pathlib import Path

import frappe

SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
OTHER_SITE = "erp-pos-b.localhost" if SITE != "erp-pos-b.localhost" else "erp-pos-a.localhost"
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
CASHIER = "erp-pos-cashier@example.com"


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True
    return site


def ensure_user(email, roles):
    if frappe.db.exists("User", email):
        return
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": email.split("@")[0],
        "send_welcome_email": 0,
        "roles": [{"role": role} for role in roles],
    })
    user.insert(ignore_permissions=True)


def main():
    site = connect()
    frappe.set_user("Administrator")
    ensure_user(OWNER, ["System Manager"])
    ensure_user(CASHIER, ["Desk User"])

    from pearnly_erp_portal import purchase

    frappe.set_user(OWNER)
    created = None
    existing = frappe.db.get_value("Purchase Document", {"dedupe_key": "lab-probe-1"}, "name")
    if existing:
        created = existing
    else:
        created = purchase.create_purchase(
            supplier_name="Lab Supplier",
            doc_date="2026-09-21",
            lines=[{"description": "Lab item", "qty": 2, "unit_price": 10}],
            dedupe_key="lab-probe-1",
        )

    owner_view = purchase.list_purchases(limit=5)

    frappe.set_user(CASHIER)
    cashier_read = frappe.has_permission("Purchase Document", "read")
    cashier_view = None
    cashier_error = None
    try:
        cashier_view = purchase.list_purchases(limit=5)
    except Exception as exc:  # noqa: BLE001 - the point is to record what happens
        cashier_error = f"{type(exc).__name__}: {exc}"

    roles = sorted(frappe.get_roles(CASHIER))

    # Tenant isolation is structural: each tenant is its own Site and database, so the
    # other tenant must not be able to see this document at all.
    frappe.destroy()
    connect(OTHER_SITE)
    other_rows = frappe.db.count("Purchase Document")
    other_has_probe = frappe.db.exists("Purchase Document", {"dedupe_key": "lab-probe-1"})

    print(json.dumps({
        "site": site,
        "created": created,
        "owner_rows": len(owner_view),
        "cashier_roles": roles,
        "cashier_has_read_permission": bool(cashier_read),
        "cashier_api_rows": None if cashier_view is None else len(cashier_view),
        "cashier_api_error": cashier_error,
        "other_site": OTHER_SITE,
        "other_site_document_rows": other_rows,
        "other_site_has_probe_document": bool(other_has_probe),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
