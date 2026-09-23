"""Real local MariaDB/DocType/permission checks. No external API calls."""
import os
import secrets

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_dms.operators import roster
from pearnly_dms.connections import api as connections
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS", label)

try:
    existing_url = frappe.db.get_value("Pearnly ERP Endpoint",
        {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}, "system_url")
    if existing_url and not existing_url.startswith("https://example.test/"):
        raise SystemExit("Refusing to replace an imported external test connection with offline fixtures")
    connection = connections.save_connection("lab-owner", "lab-owner-secret", "https://example.test/dms/index.php")
    shared = connection["endpoint"]["name"]
    suffix = secrets.token_hex(4)
    first = roster.create_operator("Lab Sales A", "lab-a-" + suffix, "lab-password-a")
    second = roster.create_operator("Lab Sales B", "lab-b-" + suffix, "lab-password-b")
    # Product rows carry user_id; keep keys explicit so UI/API contract drift is caught.
    user_a, user_b = first["user"], second["user"]
    ep_a = resolve_dms_endpoint(user_a, None)
    ep_b = resolve_dms_endpoint(user_b, None)
    check(bool(ep_a and ep_b and ep_a != ep_b), "each operator resolves a distinct owned endpoint")
    check(resolve_dms_endpoint(user_a, ep_b["name"]) is None, "operator cannot select another employee connection")
    check(resolve_dms_endpoint(user_a, shared) is None, "operator cannot fall back to company credentials")
    check(connections.get_connection()["endpoint"]["name"] == shared, "connection screen still selects the company connection")
    endpoint = frappe.get_doc("Pearnly ERP Endpoint", ep_a["name"])
    check(endpoint.get_password("password") == "lab-password-a", "encrypted operator password reaches adapter source")
    original_username = endpoint.username
    try:
        roster.update_operator(first["name"], dms_password="must-not-be-applied", dms_username="lab-a-renamed")
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("operator username must remain immutable")
    endpoint.reload()
    check(endpoint.username == original_username and endpoint.get_password("password") == "lab-password-a",
          "rejected username change leaves identity and password unchanged")
    roster.update_operator(first["name"], dms_password="lab-password-a-new", dms_username=original_username)
    endpoint.reload()
    check(endpoint.username == original_username and endpoint.get_password("password") == "lab-password-a-new",
          "password change synchronizes the owned endpoint without changing identity")
    connections.save_connection("lab-owner-new", "lab-owner-secret-new", "https://example.test/updated-dms/index.php")
    endpoint.reload()
    check(endpoint.admin_username == "lab-owner-new" and endpoint.get_password("admin_password") == "lab-owner-secret-new",
          "company read credentials propagate without changing sales credentials")
    check(endpoint.username == original_username and endpoint.system_url.endswith("/updated-dms/index.php"), "organization URL updates preserve employee identity")
    connections.set_connection_enabled(0)
    check(resolve_dms_endpoint(user_a, ep_a["name"]) is None, "company suspension also suspends employee writes")
    connections.set_connection_enabled(1)
    roster.set_status(first["name"], "inactive")
    check(resolve_dms_endpoint(user_a, None) is None, "inactive operator has no usable endpoint")
    roster.set_status(first["name"], "active")
    check(resolve_dms_endpoint(user_a, None) == ep_a, "re-enabled operator retains own endpoint")
    roster.delete_operator(second["name"])
    check(not frappe.db.get_value("User", user_b, "enabled"), "delete revokes local identity without erasing audit User")
    check(not frappe.db.get_value("Pearnly ERP Endpoint", ep_b["name"], "enabled"), "delete retains a disabled historical endpoint")
    check(resolve_dms_endpoint(user_b, ep_b["name"]) is None, "deleted operator cannot reuse its endpoint")
    frappe.db.commit()
    frappe.destroy()
    frappe.init(site="dms-line-lab-b.localhost", sites_path=".")
    frappe.connect()
    frappe.set_user("Administrator")
    check(not frappe.db.exists("User", user_a), "second Site cannot see first Site operator")
    check(resolve_dms_endpoint(user_a, ep_a["name"]) is None, "cross-Site operator and endpoint references are rejected")
finally:
    frappe.destroy()
