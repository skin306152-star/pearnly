#!/usr/bin/env python3
"""Add only the private employee identity broker; preserve realm/login settings.

Run as the deployment operator. Secrets stay in a mode-0600 server file and are
never emitted. Re-running preserves the ownership key and client credential.
"""
import json
import os
from pathlib import Path
import secrets
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class Keycloak:
    def __init__(self, base, realm, admin_user, admin_password):
        self.base, self.realm = base.rstrip("/"), realm
        self.token = self.request("/realms/master/protocol/openid-connect/token", "POST", {
            "grant_type": "password", "client_id": "admin-cli", "username": admin_user,
            "password": admin_password,
        }, form=True)["access_token"]

    def request(self, path, method="GET", data=None, form=False, token=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        bearer = token or getattr(self, "token", None)
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
        try:
            with urlopen(Request(self.base + path, data=body, headers=headers, method=method), timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raise RuntimeError(f"Keycloak {method} {path} failed: HTTP {exc.code}") from None


def configure(api, existing=None):
    """API adapter is injectable for the isolated, real-Keycloak release lab."""
    prefix = f"/admin/realms/{api.realm}"
    profile = api.request(prefix + "/users/profile")
    attributes = profile.setdefault("attributes", [])
    # Issued usernames/emails are tenant identity keys, not self-service fields.
    # Keep account-console visibility but prohibit a customer from rebinding them.
    for item in attributes:
        if item.get("name") in {"username", "email"}:
            item.setdefault("permissions", {})["edit"] = ["admin"]
    attribute = next((a for a in attributes if a.get("name") == "pearnly_member_owner"), None)
    if attribute is None:
        attribute = {"name": "pearnly_member_owner"}
        attributes.append(attribute)
    attribute.update({"permissions": {"view": ["admin"], "edit": ["admin"]}, "multivalued": False})
    attribute.pop("required", None)
    api.request(prefix + "/users/profile", "PUT", profile)
    readback = api.request(prefix + "/users/profile")
    actual = next(a for a in readback["attributes"] if a["name"] == "pearnly_member_owner")
    if actual.get("permissions") != attribute["permissions"] or actual.get("required"):
        raise RuntimeError("Employee ownership attribute protection failed readback")
    if any(item.get("permissions", {}).get("edit") != ["admin"]
           for item in readback["attributes"] if item.get("name") in {"username", "email"}):
        raise RuntimeError("Issued account identity fields are still user-editable")
    api.request(prefix, "PUT", {"editUsernameAllowed": False})
    if api.request(prefix).get("editUsernameAllowed") is not False:
        raise RuntimeError("Username editing policy failed readback")

    client_id = "pearnly-member-identity"
    clients = prefix + "/clients"
    found = api.request(clients + "?" + urlencode({"clientId": client_id}))
    settings = {"clientId": client_id, "enabled": True, "publicClient": False,
                "serviceAccountsEnabled": True, "standardFlowEnabled": False,
                "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
                "fullScopeAllowed": False}
    if not found:
        api.request(clients, "POST", settings)
        found = api.request(clients + "?" + urlencode({"clientId": client_id}))
    if len(found) != 1:
        raise RuntimeError("Employee client identity is ambiguous")
    cid = found[0]["id"]
    api.request(clients + "/" + cid, "PUT", settings)
    user = api.request(clients + f"/{cid}/service-account-user")["id"]
    manager = api.request(clients + "?clientId=realm-management")[0]["id"]
    role_names = {"view-users", "manage-users"}
    roles = [api.request(clients + f"/{manager}/roles/{name}") for name in sorted(role_names)]
    mapping = prefix + f"/users/{user}/role-mappings/clients/{manager}"
    scopes = clients + f"/{cid}/scope-mappings/clients/{manager}"
    for path in (mapping, scopes):
        present = api.request(path) or []
        if {r["name"] for r in present} - role_names:
            raise RuntimeError("Employee client has unexpected administrator roles; refusing to broaden it")
        api.request(path, "POST", roles)
        if {r["name"] for r in api.request(path)} != role_names:
            raise RuntimeError("Employee client role readback failed")
    secret = api.request(clients + f"/{cid}/client-secret")["value"]
    token = api.request(f"/realms/{api.realm}/protocol/openid-connect/token", "POST", {
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
    }, form=True)["access_token"]
    api.request(prefix + "/users?max=1", token=token)
    owner_key = (existing or {}).get("pearnly_member_identity_ownership_key") or secrets.token_urlsafe(48)
    if len(owner_key) < 32:
        raise RuntimeError("Existing identity ownership key is too short")
    return {"pearnly_member_identity_issuer": f"http://keycloak:8080/realms/{api.realm}",
            "pearnly_member_identity_admin_client_id": client_id,
            "pearnly_member_identity_admin_client_secret": secret,
            "pearnly_member_identity_ownership_key": owner_key}


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run on the production host as the deployment operator")
    root = Path("/opt/pearnly/secrets")
    env = dict(line.split("=", 1) for line in (root / "runtime.env").read_text().splitlines() if "=" in line)
    path = root / "member-identity.json"
    existing = json.loads(path.read_text()) if path.exists() else None
    api = Keycloak("http://127.0.0.1:8180", "pearnly-business", "pearnly-bootstrap", env["KEYCLOAK_ADMIN_PASSWORD"])
    values = configure(api, existing)
    # Cowork has its own account pool (split from ERP / DMS on 2026-09-26). Once bootstrap-auth.py
    # --realm pearnly-cowork has created it, give the member service a client there too
    # (keys pearnly_member_identity_cowork_*; the ownership key stays shared).
    if any(row["realm"] == "pearnly-cowork" for row in api.request("/admin/realms")):
        cowork = configure(Keycloak("http://127.0.0.1:8180", "pearnly-cowork", "pearnly-bootstrap",
                                    env["KEYCLOAK_ADMIN_PASSWORD"]), values)
        values.update({key.replace("pearnly_member_identity_", "pearnly_member_identity_cowork_"): cowork[key]
                       for key in ("pearnly_member_identity_issuer", "pearnly_member_identity_admin_client_id",
                                   "pearnly_member_identity_admin_client_secret")})
    temporary = root / "member-identity.json.new"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(values, stream)
    os.replace(temporary, path)
    print("Private employee identity client and protected ownership attribute verified; secrets suppressed")


if __name__ == "__main__":
    main()
