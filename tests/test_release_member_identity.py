"""Opt-in, disposable release lab only: real MariaDB/Frappe/Keycloak account chain.

Run in the internal-network release bench, passing the production configurator on
stdin. No production credentials, real users, paid APIs or messages are touched.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from urllib.parse import urlencode
from unittest.mock import patch

import frappe
import requests
from werkzeug.serving import make_server, WSGIRequestHandler

CONTROL = "release-control.localhost"
ERP = "release-erp.localhost"
REALM = "pearnly-member-release-lab"
BASE = "http://keycloak:8080"
ROOT_TOKEN = "release-member-billing-root-only"


def init(site):
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")


def configure_site(site, values):
    from frappe.installer import update_site_config
    init(site)
    for key, value in values.items():
        update_site_config(key, value)
        frappe.conf[key] = value
    frappe.clear_cache()
    frappe.db.commit()
    frappe.destroy()


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    assert os.environ.get("PEARNLY_RELEASE_LAB") == "1", "Disposable lab only"
    assert Path.cwd() == Path("/home/frappe/frappe-bench")
    os.chdir("/home/frappe/frappe-bench/sites")
    namespace = {"__name__": "release_member_config"}
    exec(compile(sys.stdin.read(), "configure-member-identity.py", "exec"), namespace)
    api = namespace["Keycloak"](BASE, REALM, "release-lab-admin", "unified-release-lab-only")
    realms = api.request("/admin/realms")
    if not any(row["realm"] == REALM for row in realms):
        api.request("/admin/realms", "POST", {"realm": REALM, "enabled": True,
            "sslRequired": "none", "registrationAllowed": False, "loginWithEmailAllowed": True,
            "duplicateEmailsAllowed": False, "passwordPolicy": ""})
    prefix = "/admin/realms/" + REALM
    profile = api.request(prefix + "/users/profile")
    for attribute in profile["attributes"]:
        if attribute["name"] in ("firstName", "lastName"):
            attribute.pop("required", None)
    api.request(prefix + "/users/profile", "PUT", profile)
    init(CONTROL)
    saved = {key: frappe.conf.get(key) for key in (
        "pearnly_member_identity_issuer", "pearnly_member_identity_admin_client_id",
        "pearnly_member_identity_admin_client_secret", "pearnly_member_identity_ownership_key")}
    for doctype in ("Pearnly Member Identity", "Pearnly Member Operation"):
        assert frappe.db.table_exists(doctype), doctype
    frappe.destroy()
    configured = namespace["configure"](api, saved)
    configure_site(CONTROL, {**configured, "pearnly_billing_only": True,
        "pearnly_billing_scoped_tokens": True, "pearnly_billing_token": ROOT_TOKEN})
    verify_client = "release-member-verify"
    if not api.request(prefix + "/clients?clientId=" + verify_client):
        api.request(prefix + "/clients", "POST", {"clientId": verify_client,
            "publicClient": True, "enabled": True, "directAccessGrantsEnabled": True,
            "standardFlowEnabled": False})

    def login(username, password):
        return requests.post(BASE + f"/realms/{REALM}/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": verify_client,
            "username": username, "password": password, "scope": "openid"}, timeout=15)

    from frappe import app as frappe_app
    frappe_app._sites_path = "/home/frappe/frappe-bench/sites"
    application = frappe_app.application
    server = make_server("127.0.0.1", 0, application, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    from pearnly_control.billing_auth import tenant_token
    from pearnly_control.account import member_provider, member_service
    suffix = uuid.uuid4().hex[:10]
    tenant, other = "member-lab-" + suffix, "member-lab-other-" + suffix
    init(CONTROL)
    for name in (tenant, other):
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": name,
            "status": "active"}).insert(ignore_permissions=True, set_name=name)
    frappe.db.commit()
    frappe.destroy()

    def rpc(action="create", email=None, username=None, password="fixture-password", tenant_value=tenant,
            product="erp", request_id=None, token=None):
        response = requests.post(endpoint + "/api/method/pearnly_control.account.member_api.execute",
            headers={"Host": CONTROL, "X-Pearnly-Billing-Product": product,
                "X-Pearnly-Billing-Token": token or tenant_token(ROOT_TOKEN, tenant_value, product)},
            json={"tenant": tenant_value, "action": action, "email": email or f"{suffix}@example.test",
                "username": username or "Member" + suffix, "password": password,
                "request_id": request_id or uuid.uuid4().hex}, timeout=60)
        assert response.status_code == 200, (response.status_code, response.text[:160])
        return response.json()["message"]

    try:
        created = rpc()
        assert created["ok"], created
        subject = created["subject"]
        username, email = "Member" + suffix, f"{suffix}@example.test"
        assert login(username, "fixture-password").status_code == 200
        assert login(email, "fixture-password").status_code == 200
        assert rpc()["subject"] == subject
        assert not rpc(password="different-password")["ok"]
        assert not rpc(tenant_value=other)["ok"]
        assert not rpc(tenant_value=other, email=f"other-{suffix}@example.test")["ok"]
        assert not rpc(product="dms")["ok"]
        assert not rpc(token=ROOT_TOKEN)["ok"]
        assert not rpc(tenant_value=other, token=tenant_token(ROOT_TOKEN, tenant, "erp"))["ok"]
        assert not rpc(action="delete")["ok"]
        # An existing non-member/owner identity must not be adopted by email.
        owner_username, owner_email = "Owner" + suffix, f"owner-{suffix}@example.test"
        api.request(prefix + "/users", "POST", {"username": owner_username, "email": owner_email,
            "emailVerified": True, "enabled": True, "firstName": owner_username,
            "credentials": [{"type": "password", "value": "owner-original", "temporary": False}]})
        assert not rpc(email=owner_email, username=owner_username, password="takeover-attempt")["ok"]
        assert not rpc(action="reset-password", email=owner_email, password="takeover-attempt")["ok"]
        assert login(owner_username, "owner-original").status_code == 200
        print("PASS real private RPC: create/email+username login/replay/scope/tenant/owner isolation")

        init(CONTROL)
        from frappe import recorder
        recorder.start(record_sql=False, capture_stack=False, capture_doc_events=False)
        recorded_before = set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH))
        frappe.destroy()
        assert rpc()["ok"]
        assert not rpc(token="bad-token")["ok"]
        for route in ("/api/v1/method/pearnly_control.account.member_api.execute",
                      "/api/method/pearnly_control.account.member_api.execute"):
            denied = requests.get(endpoint + route, headers={"Host": CONTROL},
                json={"password": "recorder-denial-password"}, timeout=15)
            assert denied.status_code in (403, 405), denied.status_code
            assert denied.headers["Cache-Control"] == "no-store"
        init(CONTROL)
        assert set(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH)) == recorded_before
        # A non-account request is still recorded; the global switch was not disabled.
        frappe.destroy()
        requests.post(endpoint + "/api/method/pearnly_control.billing_api.service_wallet",
            headers={"Host": CONTROL}, json={"tenant": tenant}, timeout=15)
        init(CONTROL)
        assert len(frappe.cache.hgetall(recorder.RECORDER_REQUEST_HASH)) > len(recorded_before)
        frappe.client_cache.set_value(recorder.RECORDER_INTERCEPT_FLAG, False)
        recorder.RecorderConfig.delete()
        frappe.destroy()
        print("PASS Recorder excludes successful/rejected member requests and preserves unrelated recording")

        # Direct user account APIs cannot mutate the admin-only proof.
        bearer = login(username, "fixture-password").json()["access_token"]
        before = api.request(prefix + "/users/" + subject)["attributes"]["pearnly_member_owner"]
        response = requests.post(BASE + f"/realms/{REALM}/account/",
            headers={"Authorization": "Bearer " + bearer},
            json={"email": email, "firstName": username, "attributes": {"pearnly_member_owner": ["forged"]}}, timeout=15)
        assert response.status_code in (200, 204, 400, 403), response.status_code
        assert api.request(prefix + "/users/" + subject)["attributes"]["pearnly_member_owner"] == before
        response = requests.post(BASE + f"/realms/{REALM}/account/",
            headers={"Authorization": "Bearer " + bearer}, json={
                "username": "Changed" + suffix, "email": f"changed-{suffix}@example.test",
                "firstName": username}, timeout=15)
        assert response.status_code in (200, 204, 400, 403), response.status_code
        current = api.request(prefix + "/users/" + subject)
        assert current["username"].lower() == username.lower() and current["email"] == email
        assert login(username, "fixture-password").status_code == 200
        assert login(email, "fixture-password").status_code == 200
        print("PASS Keycloak user cannot change ownership/username/email; both login identifiers still work")

        # Provider really commits, then the response is deliberately lost.
        create_original = member_provider.MemberProvider.create
        loss = {"sent": False}

        def create_lost(provider, **kwargs):
            result = create_original(provider, **kwargs)
            if not loss["sent"]:
                loss["sent"] = True
                raise member_provider.MemberError("unavailable")
            return result

        recovery = {"email": f"lost-{suffix}@example.test", "username": "Lost" + suffix}
        with patch.object(member_provider.MemberProvider, "create", create_lost):
            assert not rpc(**recovery)["ok"]
            assert not rpc(**recovery, password="wrong-retry")["ok"]
            restored = rpc(**recovery)
            assert restored["ok"], restored
        assert login(recovery["username"], "fixture-password").status_code == 200
        print("PASS create lost-response recovery rejects changed password and preserves original login")

        reset_original = member_provider.MemberProvider.reset_password
        loss = {"sent": False}

        def reset_lost(provider, *args, **kwargs):
            reset_original(provider, *args, **kwargs)
            if not loss["sent"]:
                loss["sent"] = True
                raise member_provider.MemberError("unavailable")

        reset_id = uuid.uuid4().hex
        with patch.object(member_provider.MemberProvider, "reset_password", reset_lost):
            assert not rpc(action="reset-password", password="reset-value", request_id=reset_id)["ok"]
            blocked = rpc(action="reset-password", password="different-reset")
            assert blocked == {"ok": False, "error": "reset_pending"}, blocked
            assert rpc(action="reset-password", password="reset-value", request_id=reset_id)["ok"]
        assert login(username, "reset-value").status_code == 200
        assert login(username, "fixture-password").status_code != 200
        assert rpc(action="reset-password", password="reset-new")["ok"]
        assert rpc(action="reset-password", password="reset-value", request_id=reset_id)["ok"]
        assert login(username, "reset-new").status_code == 200  # old completed request is a no-op
        init(CONTROL)
        member_key = member_service._member_key(tenant, email)
        frappe.db.set_value("Pearnly Member Identity", member_key, "subject", "not-the-verified-subject")
        frappe.db.commit()
        frappe.destroy()
        assert not rpc(action="reset-password", password="tamper-guard")["ok"]
        assert login(username, "reset-new").status_code == 200
        init(CONTROL)
        frappe.db.set_value("Pearnly Member Identity", member_key, "subject", subject)
        frappe.db.commit()
        frappe.destroy()
        assert rpc(action="reset-password", password="tamper-guard")["ok"]
        print("PASS real password reset/unknown outcome recovery/stale replay protection")

        # Concurrent create is serialized and returns one identity.
        concurrent_payload = {"email": f"parallel-{suffix}@example.test", "username": "Parallel" + suffix}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: rpc(**concurrent_payload), range(2)))
        # A transaction may need a bounded client retry; this cannot create a second user.
        results = [result if result["ok"] else rpc(**concurrent_payload) for result in results]
        assert all(result["ok"] for result in results), results
        assert results[0]["subject"] == results[1]["subject"]
        print("PASS concurrent create converges to one identity")

        # Teardown uses only management-owned functions, never the scoped RPC.
        cleanup_tenant = "member-lab-cleanup-" + suffix
        init(CONTROL)
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": cleanup_tenant,
            "status": "active"}).insert(ignore_permissions=True, set_name=cleanup_tenant)
        frappe.db.commit()
        frappe.destroy()
        cleanup_email, cleanup_username = f"cleanup-{suffix}@example.test", "Cleanup" + suffix
        cleanup = rpc(tenant_value=cleanup_tenant, email=cleanup_email, username=cleanup_username)
        assert cleanup["ok"], cleanup
        cleanup_key = member_service._member_key(cleanup_tenant, cleanup_email)
        # A forged persisted subject cannot make lifecycle deletion touch an owner.
        owner_subject = api.request(prefix + "/users?" + urlencode({"email": owner_email, "exact": "true"}))[0]["id"]
        init(CONTROL)
        frappe.db.set_value("Pearnly Member Identity", cleanup_key, "subject", owner_subject)
        frappe.db.commit()
        try:
            member_service.prepare_purge(cleanup_tenant)
        except member_provider.MemberError:
            frappe.db.rollback()
        else:
            raise AssertionError("Foreign subject accepted for teardown")
        assert frappe.db.get_value("Pearnly Tenant", cleanup_tenant, "status") == "active"
        frappe.db.set_value("Pearnly Member Identity", cleanup_key, "subject", cleanup["subject"])
        frappe.db.commit()
        frappe.destroy()
        assert login(owner_username, "owner-original").status_code == 200
        loss = {"sent": False}
        pending_email, pending_username = f"cleanup-lost-{suffix}@example.test", "CleanupLost" + suffix
        with patch.object(member_provider.MemberProvider, "create", create_lost):
            assert not rpc(tenant_value=cleanup_tenant, email=pending_email, username=pending_username)["ok"]
        init(CONTROL)
        prepared = member_service.prepare_purge(cleanup_tenant)
        assert prepared["members"] == 2 and len(prepared["subjects"]) == 2, prepared
        frappe.db.commit()
        frappe.destroy()
        assert not rpc(tenant_value=cleanup_tenant, email=cleanup_email, username=cleanup_username)["ok"]
        assert not rpc(action="reset-password", tenant_value=cleanup_tenant, email=cleanup_email)["ok"]
        init(CONTROL)
        # Simulate a process ending after one external deletion but before DB cleanup.
        deletion_original = member_provider.MemberProvider.delete_owned
        deleted = {"count": 0}

        def delete_lost(provider, *args, **kwargs):
            deletion_original(provider, *args, **kwargs)
            deleted["count"] += 1
            if deleted["count"] == 1:
                raise member_provider.MemberError("unavailable")

        with patch.object(member_provider.MemberProvider, "delete_owned", delete_lost):
            try:
                member_service.purge_members(cleanup_tenant)
            except member_provider.MemberError:
                frappe.db.rollback()
            else:
                raise AssertionError("Expected lost teardown reply")
        assert frappe.db.count("Pearnly Member Identity", {"tenant": cleanup_tenant}) == 2
        assert member_service.purge_members(cleanup_tenant)["members"] == 2
        frappe.db.commit()
        assert member_service.purge_members(cleanup_tenant)["members"] == 0
        assert not frappe.db.count("Pearnly Member Identity", {"tenant": cleanup_tenant})
        frappe.destroy()
        assert login(cleanup_username, "fixture-password").status_code != 200
        assert login(pending_username, "fixture-password").status_code != 200
        assert login(owner_username, "owner-original").status_code == 200
        print("PASS teardown preflight/freeze/pending-create recovery/exact deletion/404 replay/foreign owner protection")

        configure_site(ERP, {"pearnly_product": "erp", "pearnly_tenant": tenant,
            "pearnly_native_account": True, "pearnly_oidc_issuer": BASE + f"/realms/{REALM}",
            "pearnly_billing_api": endpoint, "pearnly_billing_host_header": CONTROL,
            "pearnly_billing_token": tenant_token(ROOT_TOKEN, tenant, "erp")})
        init(ERP)
        from pearnly_erp_portal.team import service
        for role in ("ERP Admin", "ERP User", "ERP Products"):
            if not frappe.db.exists("Role", role):
                frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
        boss = f"boss-{suffix}@example.test"
        frappe.get_doc({"doctype": "User", "email": boss, "first_name": "Boss" + suffix,
            "send_welcome_email": 0, "roles": [{"role": "ERP Admin"}]}).insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.set_user(boss)
        member_email, member_username = f"erp-{suffix}@example.test", "Erp" + suffix
        result = service.add_member(username=member_username, email=member_email,
            roles=["ERP Products"], password="erp-initial")
        assert result["email"] == member_email
        bound = service._bound_subject(member_email)
        assert bound
        assert not frappe.db.sql("select name from `__Auth` where doctype='User' and name=%s", member_email)
        assert login(member_username, "erp-initial").status_code == 200
        service.set_member_password(member_email, "erp-reset")
        assert login(member_username, "erp-reset").status_code == 200
        service.set_member_active(member_email, 0)
        from pearnly_core import identity
        try:
            identity._resolve_user(BASE + f"/realms/{REALM}", bound)
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Disabled member accepted")
        service.set_member_active(member_email, 1)
        assert identity._resolve_user(BASE + f"/realms/{REALM}", bound) == member_email
        frappe.db.commit()
        frappe.destroy()
        print("PASS ERP Admin add/bind/reset/disable/enable with no local password or admin secret")
        init(CONTROL)
        assert not frappe.db.exists("Error Log", {"error": ["like", "%fixture-password%"]})
        frappe.destroy()
        print("PASS no fixture password captured in Control Error Log")
    finally:
        frappe.destroy()
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
