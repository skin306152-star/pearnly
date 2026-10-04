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
TENANT2 = "T-2026-0002"
ROOT_TOKEN = "lab-billing-root-token"
ISSUER = "http://keycloak:8080/realms/pearnly-business"


def site_config(site, **values):
    assert site in ("erp.lab", "erp2.lab", "billing.lab")
    path = f"{site}/site_config.json"
    data = json.load(open(path))
    data.update(values)
    json.dump(data, open(path, "w"), indent=1)


def run(site, fn):
    assert site in ("erp.lab", "erp2.lab", "billing.lab")
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

    for tenant in (TENANT, TENANT2):
        if not frappe.db.exists("Pearnly Tenant", tenant):
            frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "Lab " + tenant, "status": "active"}).insert(
                ignore_permissions=True, set_name=tenant)
        # The same person owns both lab tenants (one identity, two sites).
        member_service.register_owner(tenant, facts["subjects"]["owner@lab.test"])
        member_service.register_owner(tenant, facts["subjects"]["owner@lab.test"])  # idempotent
        facts["token_" + tenant] = tenant_token(ROOT_TOKEN, tenant, "erp")
    # The tenant-2 account of a different person (own Keycloak account) — same LINE as a tenant-1 user later.
    member_service.register_owner(TENANT2, facts["subjects"]["other@lab.test"])
    print("tenant identities:", frappe.get_all("Pearnly Tenant Identity", fields=["tenant", "subject", "role"]))


run("billing.lab", billing)


def erp_config(site, tenant):
    site_config(site, host_name=f"http://{site}:8000", pearnly_product="erp", pearnly_tenant=tenant,
                pearnly_oidc_issuer=ISSUER, pearnly_oidc_client_id=site, pearnly_oidc_client_secret=site + "-oidc",
                pearnly_oidc_redirect_uri=f"http://{site}:8000/api/method/pearnly_core.identity.callback",
                pearnly_native_account=True, pearnly_billing_api="http://billing.lab:8000",
                pearnly_billing_host_header="billing.lab", pearnly_billing_token=facts["token_" + tenant],
                pearnly_line_liff_id="2010411313-lab" + site.split(".")[0], pearnly_line_login_idp=facts["alias"])


def erp_users(emails):
    def create():
        for email in emails:
            if not frappe.db.exists("User", email):
                frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "enabled": 1,
                                "user_type": "System User", "send_welcome_email": 0,
                                "roles": [{"role": "System Manager"}]}).insert(ignore_permissions=True)
            if not frappe.db.exists("Pearnly Identity", {"user": email}):
                frappe.get_doc({"doctype": "Pearnly Identity", "issuer": ISSUER, "subject": facts["subjects"][email],
                                "user": email, "status": "active"}).insert(ignore_permissions=True)
        print(frappe.local.site, "identities:", frappe.get_all("Pearnly Identity", fields=["user"]))
        print("binding columns:", [c for c in frappe.db.get_table_columns("Pearnly LINE Binding") if "login" in c])
    return create


erp_config("erp.lab", TENANT)
run("erp.lab", erp_users(("owner@lab.test", "stranger@lab.test")))
erp_config("erp2.lab", TENANT2)
run("erp2.lab", erp_users(("owner@lab.test", "other@lab.test")))
json.dump(facts, open("/tmp/lab-facts.json", "w"))
