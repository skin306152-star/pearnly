"""LINE sign-in lab, step 2: billing (member service) and ERP site configuration. Synthetic only."""
import json
import os

# Laboratory only: refuse anything but the two synthetic lab sites and the lab Keycloak.
if os.environ.get("PEARNLY_LINE_LOGIN_LAB") != "1":
    raise SystemExit("Run through scripts/line-login-lab.sh inside the lab bench only")

os.chdir("/home/frappe/frappe-bench/sites")
import frappe  # noqa: E402

facts = json.load(open("/tmp/lab-facts.json"))
TENANT = "T-2026-0001"
ROOT_TOKEN = "lab-billing-root-token"
ISSUER = "http://keycloak:8080/realms/pearnly-business"


def site_config(site, **values):
    assert site in ("erp.lab", "billing.lab")
    path = f"{site}/site_config.json"
    data = json.load(open(path))
    data.update(values)
    json.dump(data, open(path, "w"), indent=1)


def run(site, fn):
    assert site in ("erp.lab", "billing.lab")
    frappe.init(site=site, sites_path=".")
    frappe.connect()
    try:
        fn()
        frappe.db.commit()
    finally:
        frappe.destroy()


site_config("billing.lab", pearnly_billing_only=1, pearnly_billing_scoped_tokens=1, pearnly_billing_token=ROOT_TOKEN,
            pearnly_member_identity_issuer=ISSUER, pearnly_member_identity_admin_client_id="pearnly-member-identity",
            pearnly_member_identity_admin_client_secret=facts["member_secret"],
            pearnly_member_identity_ownership_key="lab-ownership-key-" + "x" * 40)


def billing():
    from pearnly_control.account import member_service
    from pearnly_control.billing_auth import tenant_token

    if not frappe.db.exists("Pearnly Tenant", TENANT):
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "Lab Co", "status": "active"}).insert(
            ignore_permissions=True, set_name=TENANT)
    member_service.register_owner(TENANT, facts["subjects"]["owner@lab.test"])
    member_service.register_owner(TENANT, facts["subjects"]["owner@lab.test"])  # idempotent
    print("tenant identities:", frappe.get_all("Pearnly Tenant Identity", fields=["tenant", "subject", "role"]))
    facts["erp_token"] = tenant_token(ROOT_TOKEN, TENANT, "erp")


run("billing.lab", billing)

site_config("erp.lab", host_name="http://erp.lab:8000", pearnly_product="erp", pearnly_tenant=TENANT,
            pearnly_oidc_issuer=ISSUER, pearnly_oidc_client_id="erp.lab", pearnly_oidc_client_secret="erp-lab-oidc",
            pearnly_oidc_redirect_uri="http://erp.lab:8000/api/method/pearnly_core.identity.callback",
            pearnly_native_account=True, pearnly_billing_api="http://billing.lab:8000",
            pearnly_billing_host_header="billing.lab", pearnly_billing_token=facts["erp_token"],
            pearnly_line_liff_id="2010411313-labliff", pearnly_line_login_idp=facts["alias"])


def erp():
    for email, first in (("owner@lab.test", "Lab Owner"), ("stranger@lab.test", "Lab Stranger")):
        if not frappe.db.exists("User", email):
            frappe.get_doc({"doctype": "User", "email": email, "first_name": first, "enabled": 1,
                            "user_type": "System User", "send_welcome_email": 0,
                            "roles": [{"role": "System Manager"}]}).insert(ignore_permissions=True)
        if not frappe.db.exists("Pearnly Identity", {"user": email}):
            frappe.get_doc({"doctype": "Pearnly Identity", "issuer": ISSUER, "subject": facts["subjects"][email],
                            "user": email, "status": "active"}).insert(ignore_permissions=True)
    print("identities:", frappe.get_all("Pearnly Identity", fields=["user", "subject"]))
    print("binding columns:", [c for c in frappe.db.get_table_columns("Pearnly LINE Binding") if "login" in c])
    print("session columns:", [c for c in frappe.db.get_table_columns("Pearnly Session")
                               if c in ("identity_provider", "line_user_id")])


run("erp.lab", erp)
json.dump(facts, open("/tmp/lab-facts.json", "w"))
