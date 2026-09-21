"""Verify the ERP portal permission baseline on the dedicated laboratory.

Exercises the real Site path: the control-plane role payload is applied through
`pearnly_core.provisioning.ensure_feature_roles`, then the whitelisted purchase
API is called as three different accounts (owner / employee / unrelated user) to
check that capability and record scope are enforced on the server.

Uses synthetic users and documents in the lab volume only. No identity, LINE,
ERP or billing service is contacted, and erpnext is not installed.
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
STAFF = "erp-pos-staff@example.com"
OUTSIDER = "erp-pos-outsider@example.com"
RUN = "lab-permission-baseline"


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
    frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": email.split("@")[0],
        "send_welcome_email": 0,
        "roles": [{"role": role} for role in roles],
    }).insert(ignore_permissions=True)


def grant_roles(site):
    """Apply the ERP feature roles exactly the way provisioning does."""
    from pearnly_core.provisioning import ensure_feature_roles

    spec = {
        "product": "erp",
        "admin_role": "ERP Admin",
        "base_roles": ["Desk User"],
        "roles": [
            {
                "role": "ERP Purchase",
                "doctypes": ["Purchase Document", "Purchase Line"],
                "feature": "purchase",
            },
        ],
    }
    report = ensure_feature_roles(json.dumps(spec), admin_user=OWNER)

    # 员工拿功能角色这一步属于团队管理（后续批次实现）。实验室先直接发角色，
    # 只为验证「有功能角色的员工能做什么、看不到什么」。
    staff = frappe.get_doc("User", STAFF)
    if "ERP Purchase" not in {r.role for r in staff.roles}:
        staff.append("roles", {"role": "ERP Purchase"})
        staff.save(ignore_permissions=True)
        frappe.db.commit()
    return report


def call_as(user, func, *args, **kwargs):
    """Return (ok, payload) without letting a thrown error stop the run."""
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, type(exc).__name__


def ensure_document(user, key, supplier, qty, price):
    """Create once; a repeat run reuses the document instead of reporting a failure."""
    existing = frappe.db.get_value("Purchase Document", {"dedupe_key": key}, "name")
    if existing:
        return True, existing
    from pearnly_erp_portal import purchase

    return call_as(user, purchase.create_purchase, supplier_name=supplier,
                   doc_date="2026-09-21",
                   lines=[{"description": supplier, "qty": qty, "unit_price": price}],
                   dedupe_key=key)


def main():
    site = connect()
    frappe.set_user("Administrator")
    ensure_user(STAFF, ["Desk User"])
    ensure_user(OUTSIDER, ["Desk User"])
    if not frappe.db.exists("User", OWNER):
        ensure_user(OWNER, ["Desk User"])
    granted = grant_roles(site)

    from pearnly_erp_portal import purchase

    results = {}
    # Owner: capability + all records.
    ok, created = ensure_document(OWNER, f"{RUN}-owner", "Lab Owner", 1, 20)
    results["owner_create"] = ok
    results["owner_document"] = created

    # Employee with the purchased feature: capability, but own records only.
    ok, staff_doc = ensure_document(STAFF, f"{RUN}-staff", "Lab Staff", 3, 5)
    results["staff_create"] = ok
    results["staff_document"] = staff_doc

    ours = {"Lab Owner", "Lab Staff"}

    ok, rows = call_as(OWNER, purchase.list_purchases, limit=50)
    owner_rows = [r for r in rows if r["supplier_name"] in ours] if ok else rows
    results["owner_visible"] = len(owner_rows) if ok else rows
    results["owner_sees_staff_document"] = ok and any(
        r["supplier_name"] == "Lab Staff" for r in owner_rows
    )

    ok, rows = call_as(STAFF, purchase.list_purchases, limit=50)
    results["staff_visible"] = len([r for r in rows if r["supplier_name"] in ours]) if ok else rows
    results["staff_sees_owner_document"] = ok and any(
        r["supplier_name"] == "Lab Owner" for r in rows
    )

    # A signed-in user with no granted feature must be refused, not served.
    ok, payload = call_as(OUTSIDER, purchase.list_purchases, limit=50)
    results["outsider_allowed"] = ok
    results["outsider_error"] = None if ok else payload

    ok, payload = call_as(OUTSIDER, purchase.create_purchase, supplier_name="Nope",
                          doc_date="2026-09-21",
                          lines=[{"description": "Nope", "qty": 1, "unit_price": 1}],
                          dedupe_key=f"{RUN}-outsider")
    results["outsider_create_allowed"] = ok

    ok, payload = call_as("Guest", purchase.list_purchases, limit=50)
    results["guest_allowed"] = ok
    results["guest_error"] = None if ok else payload

    # Owner summary must still work after switching to permission-aware queries.
    ok, summary = call_as(OWNER, purchase.summary)
    results["owner_summary"] = summary if ok else summary

    frappe.set_user("Administrator")
    results["owner_roles"] = sorted(frappe.get_roles(OWNER))
    results["staff_roles"] = sorted(frappe.get_roles(STAFF))
    results["grant_report"] = {
        "created_roles": granted.get("created_roles"),
        "removed_from_owner": granted.get("removed"),
    }

    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_document_rows"] = frappe.db.count("Purchase Document")

    expected = {
        "owner_create": True,
        "staff_create": True,
        "owner_sees_staff_document": True,
        "staff_sees_owner_document": False,
        "outsider_allowed": False,
        "outsider_error": "PermissionError",
        "outsider_create_allowed": False,
        "guest_allowed": False,
        "guest_error": "AuthenticationError",
        "other_site_document_rows": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if results.get("owner_visible") != 2:
        problems["owner_visible"] = {"expected": 2, "actual": results.get("owner_visible")}
    if results.get("staff_visible") != 1:
        problems["staff_visible"] = {"expected": 1, "actual": results.get("staff_visible")}

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
