#!/usr/bin/python3
"""Configure the new business realm, never import sample users or clients.

Run on the VPS. Optional stdin contains SMTP configuration, never logged.
All admin API calls stay on loopback; production credentials stay mode 0600.
"""
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path("/opt/pearnly/secrets")
config = dict(line.split("=", 1) for line in (ROOT / "runtime.env").read_text().splitlines() if "=" in line)
BASE = "http://127.0.0.1:8180"
REALM = "pearnly-business"


def request(path, method="GET", data=None, token=None, form=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    try:
        with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        # Never print a response containing credentials or a password grant body.
        raise SystemExit(f"Keycloak request failed: {method} {path} HTTP {exc.code}") from None


token = request("/realms/master/protocol/openid-connect/token", "POST", {
    "grant_type": "password", "client_id": "admin-cli", "username": "pearnly-bootstrap",
    "password": config["KEYCLOAK_ADMIN_PASSWORD"],
}, form=True)["access_token"]
settings = {
    "realm": REALM, "enabled": True, "displayName": "Pearnly",
    "sslRequired": "external", "registrationAllowed": False,
    "loginWithEmailAllowed": True, "duplicateEmailsAllowed": False, "editUsernameAllowed": False,
    "resetPasswordAllowed": True, "rememberMe": True, "passwordPolicy": "",
    "loginTheme": "pearnly", "emailTheme": "pearnly", "internationalizationEnabled": True,
    "supportedLocales": ["th", "zh-CN", "en", "ja"], "defaultLocale": "th",
    "bruteForceProtected": True, "permanentLockout": False,
    "failureFactor": 10, "waitIncrementSeconds": 60, "maxFailureWaitSeconds": 900,
}
if "--smtp" in sys.argv:
    settings["smtpServer"] = json.load(sys.stdin)
    if settings["smtpServer"].get("from") != "hello@pearnly.com":
        raise SystemExit("Official sender must be hello@pearnly.com")
    settings["smtpServer"].update({"replyTo": "hello@pearnly.com", "fromDisplayName": "Pearnly"})
    import smtplib
    import ssl
    smtp = settings["smtpServer"]
    with smtplib.SMTP(smtp["host"], int(smtp["port"]), timeout=30) as mail:
        mail.starttls(context=ssl.create_default_context())
        mail.login(smtp["user"], smtp["password"])
        assert mail.noop()[0] == 250
    print("Existing SMTP credentials authenticated over TLS; no email sent")
realms = request("/admin/realms", token=token)
if any(row["realm"] == REALM for row in realms):
    request(f"/admin/realms/{REALM}", "PUT", settings, token)
else:
    request("/admin/realms", "POST", settings, token)

# Provisioning collects a company, username and email, not separate personal
# first/last names. Keep native profile validation, but do not require names.
profile_path = f"/admin/realms/{REALM}/users/profile"
profile = request(profile_path, token=token)
for attribute in profile.get("attributes", []):
    if attribute.get("name") in ("username", "email"):
        attribute.setdefault("permissions", {})["edit"] = ["admin"]
    if attribute.get("name") in ("firstName", "lastName"):
        attribute.pop("required", None)
request(profile_path, "PUT", profile, token)
assert all(not attribute.get("required") for attribute in
           request(profile_path, token=token).get("attributes", [])
           if attribute.get("name") in ("firstName", "lastName"))

# Dedicated local control-plane client. Never copy this secret to tenant Sites.
clients_path = f"/admin/realms/{REALM}/clients"
client = request(clients_path + "?clientId=pearnly-control", token=token)
if not client:
    request(clients_path, "POST", {"clientId": "pearnly-control", "enabled": True,
        "publicClient": False, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False, "fullScopeAllowed": False}, token)
    client = request(clients_path + "?clientId=pearnly-control", token=token)
cid = client[0]["id"]
service_user = request(clients_path + f"/{cid}/service-account-user", token=token)["id"]
manager = request(clients_path + "?clientId=realm-management", token=token)[0]["id"]
roles = [request(clients_path + f"/{manager}/roles/{role}", token=token)
         for role in ("view-users", "manage-users", "view-clients", "manage-clients")]
request(f"/admin/realms/{REALM}/users/{service_user}/role-mappings/clients/{manager}", "POST", roles, token)
request(clients_path + f"/{cid}/scope-mappings/clients/{manager}", "POST", roles, token)
secret = request(clients_path + f"/{cid}/client-secret", token=token)["value"]
secret_file = ROOT / "control-identity.json"
fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as handle:
    json.dump({"issuer": "https://auth.pearnly.com/realms/" + REALM,
               "client_id": "pearnly-control", "client_secret": secret}, handle)
readback = request(f"/admin/realms/{REALM}", token=token)
assert all(readback.get(key) == settings[key] for key in (
    "sslRequired", "registrationAllowed", "loginWithEmailAllowed", "loginTheme",
    "defaultLocale", "bruteForceProtected", "resetPasswordAllowed", "editUsernameAllowed"))
assert not request(f"/admin/realms/{REALM}/identity-provider/instances", token=token)
print("Production realm verified: Thai default, cat theme, email/username login, reset enabled, no social/signup")
print("Local control client configured; secret stored privately, not printed")
service_token = request(f"/realms/{REALM}/protocol/openid-connect/token", "POST", {
    "grant_type": "client_credentials", "client_id": "pearnly-control", "client_secret": secret,
}, form=True)["access_token"]
request(f"/admin/realms/{REALM}/users?max=1", token=service_token)
request(clients_path + "?clientId=pearnly-control", token=service_token)
print("Control service-account user and client queries verified")
