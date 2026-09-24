"""Configure and verify real local employee identities in the immutable ERP lab.

Run in billing-bench after the acceptance Sites and Keycloak are ready. Only the
dedicated acceptance volumes, synthetic accounts and local service URLs are used.
The private broker receives admin credentials; ERP receives only its scoped token.
Secrets are written with mode 0600 and never included in output or exceptions.
--configure prepares the private Site/realm/ERP configuration; --verify exercises
the team service after billing-web is ready. The default runs both phases.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import secrets
import subprocess
from urllib.parse import urlencode

import frappe
import requests


ROOT = Path("/home/frappe/frappe-bench")
BILLING_ROOT = ROOT / "sites"
# Native Frappe logging resolves ../logs from the active sites directory.
# Keep both isolated sites volumes directly under the existing bench directory.
ERP_ROOT = ROOT / "sites-erp"
BILLING = "erp-acceptance-billing.localhost"
ERP = "erp-pos-a.localhost"
REALM = "pearnly-erp-acceptance"
BASE = "http://erp-identity.localhost:8168"
ISSUER = BASE + "/realms/" + REALM
ERP_URL = "http://erp-pos-a.localhost:8167"
BROKER = "http://billing-web:8000"
TENANT = "erp-acceptance-a"
OWNER = "acceptance-owner@example.test"
OWNER_USERNAME = "AcceptanceOwner"
MEMBER = "acceptance-buyer@example.test"
MEMBER_USERNAME = "AcceptanceBuyer"
CLIENT = "pearnly-erp-acceptance-desk"
VERIFY_CLIENT = "pearnly-erp-acceptance-verify"
STATE = BILLING_ROOT / ".erp-acceptance-identity.json"
CONFIGURATOR = Path("/opt/pearnly/deploy-src/infrastructure/production/configure-member-identity.py")


def write_private(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(values, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def guard():
    if os.environ.get("PEARNLY_ERP_ACCEPTANCE") != "1":
        raise RuntimeError("Dedicated acceptance container required")
    if not (ERP_ROOT / ".erp-acceptance").is_file():
        raise RuntimeError("Acceptance ERP volume marker missing")
    names = {p.parent.name for p in ERP_ROOT.glob("*/site_config.json")}
    if names != {"erp-pos-a.localhost", "erp-pos-b.localhost"}:
        raise RuntimeError("Unexpected ERP Sites")
    for name in names:
        config = json.loads((ERP_ROOT / name / "site_config.json").read_text())
        if not config.get("pearnly_erp_acceptance"):
            raise RuntimeError("Non-acceptance ERP Site")
    common = json.loads((ERP_ROOT / "common_site_config.json").read_text())
    if common.get("db_host") != "db":
        raise RuntimeError("Unexpected ERP database")
    existing = {p.parent.name for p in BILLING_ROOT.glob("*/site_config.json")}
    if existing - {BILLING} or (existing and not (BILLING_ROOT / ".erp-acceptance-billing").is_file()):
        raise RuntimeError("Refusing a foreign billing volume")


def bench(*args):
    result = subprocess.run(["bench", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        # CalledProcessError contains argv, including the synthetic DB credential.
        raise RuntimeError("Acceptance billing bench operation failed")


def init(site, root):
    frappe.destroy()
    os.chdir(root)
    frappe.init(site=site, sites_path=str(root))
    frappe.connect()
    frappe.flags.mute_emails = True
    frappe.set_user("Administrator")


def site_config(site, root, values):
    path = root / site / "site_config.json"
    config = json.loads(path.read_text())
    config.update(values)
    write_private(path, config)
    init(site, root)
    frappe.clear_cache()
    frappe.db.commit()
    frappe.destroy()


def keycloak():
    module = runpy.run_path(str(CONFIGURATOR), run_name="acceptance_identity_config")
    password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("Local identity bootstrap credential missing")
    return module, module["Keycloak"](BASE, REALM, "pearnly-acceptance", password)


def configure():
    common = {"db_host": "db", "redis_cache": "redis://redis-cache:6379",
        "redis_queue": "redis://redis-queue:6379/2", "redis_socketio": "redis://redis-queue:6379/2",
        "developer_mode": 0, "serve_default_site": False, "pause_scheduler": 1, "disable_scheduler": 1}
    write_private(BILLING_ROOT / "common_site_config.json", common)
    (BILLING_ROOT / ".erp-acceptance-billing").write_text("private synthetic account broker\n")
    (BILLING_ROOT / "apps.txt").write_text("frappe\npearnly_core\npearnly_control\n")
    if not (BILLING_ROOT / BILLING / "site_config.json").exists():
        password = os.environ.get("DB_ROOT_PASSWORD")
        if not password:
            raise RuntimeError("Local database bootstrap credential missing")
        bench("new-site", BILLING, "--db-host", "db", "--db-root-password", password,
              "--admin-password", secrets.token_urlsafe(32), "--mariadb-user-host-login-scope", "%")
        bench("--site", BILLING, "install-app", "pearnly_core", "pearnly_control")
    init(BILLING, BILLING_ROOT)
    required = {"frappe", "pearnly_core", "pearnly_control"}
    if set(frappe.get_installed_apps()) != required:
        raise RuntimeError("Unexpected private billing app set")
    saved = dict(frappe.conf)
    if not frappe.db.exists("Pearnly Tenant", TENANT):
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": TENANT,
            "status": "active"}).insert(ignore_permissions=True, set_name=TENANT)
    if frappe.db.get_value("Pearnly Tenant", TENANT, "status") != "active":
        raise RuntimeError("Synthetic tenant is not active")
    frappe.db.commit()
    frappe.destroy()
    print("PASS private synthetic billing Site initialized", flush=True)

    module, api = keycloak()
    prefix = "/admin/realms/" + REALM
    if not any(row["realm"] == REALM for row in api.request("/admin/realms")):
        api.request("/admin/realms", "POST", {"realm": REALM, "enabled": True,
            "sslRequired": "none", "registrationAllowed": False, "loginWithEmailAllowed": True,
            "duplicateEmailsAllowed": False, "passwordPolicy": "", "loginTheme": "pearnly",
            "internationalizationEnabled": True, "supportedLocales": ["en", "th", "zh-CN", "ja"],
            "defaultLocale": "zh-CN"})
    profile = api.request(prefix + "/users/profile")
    for attribute in profile["attributes"]:
        if attribute["name"] in ("firstName", "lastName"):
            attribute.pop("required", None)
    api.request(prefix + "/users/profile", "PUT", profile)
    private = module["configure"](api, saved)
    private["pearnly_member_identity_issuer"] = ISSUER
    root_token = saved.get("pearnly_billing_token") or secrets.token_urlsafe(48)
    site_config(BILLING, BILLING_ROOT, {**private, "pearnly_erp_acceptance": True,
        "pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
        "pearnly_billing_token": root_token, "mute_emails": 1})

    clients = prefix + "/clients"
    settings = {"clientId": CLIENT, "enabled": True, "publicClient": False,
        "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False, "implicitFlowEnabled": False,
        "redirectUris": [ERP_URL + "/api/method/pearnly_core.identity.callback"],
        "webOrigins": [ERP_URL], "attributes": {"pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": ERP_URL + "/*"}}
    found = api.request(clients + "?" + urlencode({"clientId": CLIENT}))
    if not found:
        api.request(clients, "POST", settings)
        found = api.request(clients + "?" + urlencode({"clientId": CLIENT}))
    if len(found) != 1:
        raise RuntimeError("Ambiguous acceptance browser client")
    api.request(clients + "/" + found[0]["id"], "PUT", settings)
    secret = api.request(clients + "/" + found[0]["id"] + "/client-secret")["value"]
    if not api.request(clients + "?" + urlencode({"clientId": VERIFY_CLIENT})):
        api.request(clients, "POST", {"clientId": VERIFY_CLIENT, "enabled": True,
            "publicClient": True, "directAccessGrantsEnabled": True, "standardFlowEnabled": False})
    from pearnly_control.billing_auth import tenant_token
    site_config(ERP, ERP_ROOT, {"host_name": ERP_URL, "pearnly_native_account": True,
        "pearnly_tenant": TENANT, "pearnly_oidc_issuer": ISSUER,
        "pearnly_oidc_client_id": CLIENT, "pearnly_oidc_client_secret": secret,
        "pearnly_oidc_redirect_uri": settings["redirectUris"][0],
        "pearnly_billing_api": BROKER, "pearnly_billing_host_header": BILLING,
        "pearnly_billing_token": tenant_token(root_token, TENANT, "erp")})
    if not STATE.exists():
        write_private(STATE, {"tenant": TENANT, "realm": REALM})
    print("PASS real local realm, protected broker and ERP OIDC configured; secrets suppressed", flush=True)


def verify():
    _module, api = keycloak()
    prefix = "/admin/realms/" + REALM
    state = json.loads(STATE.read_text())
    if state.get("tenant") != TENANT or state.get("realm") != REALM:
        raise RuntimeError("Unexpected identity state")
    password = "erp-acceptance-only"  # Synthetic acceptance credential, never production.
    initial = "erp-acceptance-initial-only"
    found = api.request(prefix + "/users?" + urlencode({"username": OWNER_USERNAME, "exact": "true"}))
    if not found:
        api.request(prefix + "/users", "POST", {"username": OWNER_USERNAME, "email": OWNER,
            "emailVerified": True, "enabled": True, "firstName": OWNER_USERNAME,
            "credentials": [{"type": "password", "value": password, "temporary": False}]})
        found = api.request(prefix + "/users?" + urlencode({"username": OWNER_USERNAME, "exact": "true"}))
        state["owner_subject"] = found[0]["id"]
        write_private(STATE, state)
    if (len(found) != 1 or found[0]["id"] != state.get("owner_subject")
            or found[0].get("email") != OWNER):
        raise RuntimeError("Refusing to adopt an unknown owner identity")
    init(ERP, ERP_ROOT)
    from pearnly_core import identity
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_control.features import roles_payload
    from pearnly_erp_portal.access import menu
    from pearnly_erp_portal.access.setup import sync_roles
    from pearnly_erp_portal.masters import company
    from pearnly_erp_portal.team import service
    if any(frappe.conf.get(key) for key in ("pearnly_member_identity_admin_client_secret",
            "pearnly_member_identity_ownership_key")):
        raise RuntimeError("Private identity credential present on ERP")
    created_owner = not frappe.db.exists("User", OWNER)
    if created_owner:
        frappe.get_doc({"doctype": "User", "email": OWNER, "username": OWNER_USERNAME,
            "first_name": OWNER_USERNAME, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": "Desk User"}]}).insert(ignore_permissions=True)
        # Use the production provisioning contract for the first setup. Reapplying
        # broad feature roles on every run would churn the owner's menu grants and
        # could invalidate the browser session under active acceptance.
        ensure_feature_roles(json.dumps(roles_payload("erp", frappe.conf.get("pearnly_features") or [])),
                             admin_user=OWNER)
        sync_roles()
    if not frappe.db.exists("Pearnly Identity", {"issuer": ISSUER, "subject": state["owner_subject"]}):
        identity.bind_identity(OWNER, state["owner_subject"], issuer_value=ISSUER)
    assert identity._resolve_user(ISSUER, state["owner_subject"]) == OWNER
    assert "ERP Admin" in frappe.get_roles(OWNER)
    assert "System Manager" not in frappe.get_roles(OWNER)
    frappe.db.commit()
    frappe.set_user(OWNER)
    if not company.allowed_companies():
        company.create_company(company_name="ERP Identity Acceptance", pos_enabled=True)
    existing_member = bool(frappe.db.exists("User", MEMBER))
    # Browser acceptance can switch the owner's active company. Re-verification
    # must preserve the existing employee's explicit company grants.
    assigned = (company.allowed_companies(MEMBER) if existing_member
                else [company.active_company()])
    assert assigned and set(assigned) <= set(company.allowed_companies(OWNER))
    print("PASS synthetic tenant owner provisioned without platform administration", flush=True)
    if not existing_member:
        result = service.add_member(username=MEMBER_USERNAME, email=MEMBER,
            roles=[menu.role("purchase")], companies=assigned, password=initial)
        assert result["email"] == MEMBER
    else:
        service.set_member_password(MEMBER, initial)
    subject = service._bound_subject(MEMBER)
    assert subject and identity._resolve_user(ISSUER, subject) == MEMBER
    assert not frappe.db.sql("select name from `__Auth` where doctype='User' and name=%s", MEMBER)
    assert set(company.allowed_companies(MEMBER)) == set(assigned)
    print("PASS real team member created and bound without a local password", flush=True)
    # Fixture preferences only: preserve the Site's production-matching Thai
    # default and never reset/revoke the owner's credentials or active sessions.
    for user, bound_subject in ((OWNER, state["owner_subject"]), (MEMBER, subject)):
        provider_path = prefix + "/users/" + bound_subject
        provider_user = api.request(provider_path)
        attributes = dict(provider_user.get("attributes") or {})
        if attributes.get("locale") != ["zh-CN"]:
            # Keycloak PUT is replacement semantics for profile fields; sending
            # attributes alone can clear the existing email/name projection.
            api.request(provider_path, "PUT", {**provider_user,
                "attributes": {**attributes, "locale": ["zh-CN"]}})
        actual = api.request(provider_path)
        assert actual.get("attributes", {}).get("locale") == ["zh-CN"]
        assert actual.get("email") == user and actual.get("enabled")
        frappe.db.set_value("User", user, "language", "zh")
        frappe.clear_cache(user=user)

    def login(identifier, secret):
        return requests.post(ISSUER + "/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": VERIFY_CLIENT, "username": identifier,
            "password": secret, "scope": "openid"}, timeout=20)

    for identifier in (MEMBER_USERNAME, MEMBER):
        response = login(identifier, initial)
        assert response.status_code == 200, "Initial employee login failed"
    service.set_member_password(MEMBER, password)
    assert login(MEMBER_USERNAME, initial).status_code != 200, "Old password still accepted"
    for identifier in (MEMBER_USERNAME, MEMBER, OWNER_USERNAME, OWNER):
        assert login(identifier, password).status_code == 200, "Identity login failed"
    frappe.db.commit()
    frappe.destroy()
    print("PASS real team create -> private broker -> identity binding -> username/email login -> password reset", flush=True)
    print(json.dumps({"site": ERP, "url": ERP_URL + "/desk/pearnly-team",
        "issuer": ISSUER, "owner": {"email": OWNER, "username": OWNER_USERNAME},
        "member": {"email": MEMBER, "username": MEMBER_USERNAME, "roles": [menu.role("purchase")]},
        "local_password_written": False, "fixture_language": "zh", "provider": "real local Keycloak",
        "browser_authorization_code_acceptance": "pending"}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configure", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    guard()
    if not args.verify:
        configure()
    if not args.configure:
        verify()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Frappe/provider exceptions can retain response bodies. Report only type.
        print("Acceptance identity failed: " + type(error).__name__ + "; secrets suppressed", flush=True)
        raise SystemExit(1) from None
    finally:
        frappe.destroy()
