"""LINE sign-in lab, step 1: the business realm via the production script, plus a fake LINE provider.

Writes /tmp/lab-facts.json inside the lab bench. Synthetic accounts only.
"""
import json
import os
import runpy

# Laboratory only: refuse anything but the two synthetic lab sites and the lab Keycloak.
if os.environ.get("PEARNLY_LINE_LOGIN_LAB") != "1":
    raise SystemExit("Run through scripts/line-login-lab.sh inside the lab bench only")

m = runpy.run_path("/lab/scripts/production/configure-line-login.py")
KC = "http://keycloak:8080"
root = m["Keycloak"](KC, "master", "lab-admin", "lab-admin-only")
R = "pearnly-business"
ALIAS = "line-2010411313"  # one shared entry per LINE Login channel
for realm in (R, "fakeline"):
    try:
        root.request(f"/admin/realms/{realm}", "DELETE")
    except RuntimeError:
        pass
root.request("/admin/realms", "POST", {"realm": R, "enabled": True, "registrationAllowed": False,
                                        "loginWithEmailAllowed": True, "duplicateEmailsAllowed": False})
# Production relaxed profile: names are not required.
profile = root.request(f"/admin/realms/{R}/users/profile")
for a in profile["attributes"]:
    if a["name"] in ("firstName", "lastName"):
        a.pop("required", None)
root.request(f"/admin/realms/{R}/users/profile", "PUT", profile)

# Fake LINE: ID token `sub` is a LINE-shaped user id taken from a user attribute.
root.request("/admin/realms", "POST", {"realm": "fakeline", "enabled": True})
fp = root.request("/admin/realms/fakeline/users/profile")
fp["unmanagedAttributePolicy"] = "ENABLED"
for a in fp["attributes"]:
    if a["name"] in ("firstName", "lastName", "email"):
        a.pop("required", None)
root.request("/admin/realms/fakeline/users/profile", "PUT", fp)
root.request("/admin/realms/fakeline/clients", "POST", {
    "clientId": "2010411313", "secret": "fake-line-login-secret", "publicClient": False,
    "redirectUris": [f"{KC}/realms/{R}/broker/{ALIAS}/endpoint"],
    "protocolMappers": [{"name": "line-sub", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
                         "config": {"user.attribute": "line_id", "claim.name": "sub", "jsonType.label": "String",
                                    "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"}}]})
LINES = {"lineuser1": "U" + "1" * 32, "lineuser2": "U" + "2" * 32}
for user, line_id in LINES.items():
    root.request("/admin/realms/fakeline/users", "POST", {
        "username": user, "enabled": True, "attributes": {"line_id": [line_id]},
        "credentials": [{"type": "password", "value": "line-pw", "temporary": False}]})

api = m["Keycloak"](KC, "master", "lab-admin", "lab-admin-only")
api.realm = R
# Site OIDC client (as provisioning creates it), then the production realm settings.
for site in ("erp.lab", "erp2.lab"):
    root.request(f"/admin/realms/{R}/clients", "POST", {
        "clientId": site, "secret": site + "-oidc", "publicClient": False, "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False, "redirectUris": [f"http://{site}:8000/api/method/pearnly_core.identity.callback"],
        "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": f"http://{site}:8000/"}})
m["configure_realm"](api)
fake = {"authorizationUrl": f"{KC}/realms/fakeline/protocol/openid-connect/auth",
        "tokenUrl": f"{KC}/realms/fakeline/protocol/openid-connect/token",
        "userInfoUrl": f"{KC}/realms/fakeline/protocol/openid-connect/userinfo", "issuer": ""}
site = m["configure_site"](api, alias=ALIAS, login_channel_id="2010411313",
                           login_channel_secret="fake-line-login-secret", endpoints=fake)
# Private member service client, like configure-member-identity.py.
root.request(f"/admin/realms/{R}/clients", "POST", {
    "clientId": "pearnly-member-identity", "publicClient": False, "serviceAccountsEnabled": True,
    "standardFlowEnabled": False, "directAccessGrantsEnabled": False, "fullScopeAllowed": False})
cid = root.request(f"/admin/realms/{R}/clients?clientId=pearnly-member-identity")[0]["id"]
su = root.request(f"/admin/realms/{R}/clients/{cid}/service-account-user")["id"]
mgr = root.request(f"/admin/realms/{R}/clients?clientId=realm-management")[0]["id"]
roles = [root.request(f"/admin/realms/{R}/clients/{mgr}/roles/{r}") for r in ("manage-users", "view-users")]
root.request(f"/admin/realms/{R}/users/{su}/role-mappings/clients/{mgr}", "POST", roles)
root.request(f"/admin/realms/{R}/clients/{cid}/scope-mappings/clients/{mgr}", "POST", roles)
secret = root.request(f"/admin/realms/{R}/clients/{cid}/client-secret")["value"]
subjects = {}
for email, username in (("owner@lab.test", "LabOwner"), ("stranger@lab.test", "LabStranger")):
    root.request(f"/admin/realms/{R}/users", "POST", {
        "username": username, "email": email, "enabled": True, "emailVerified": True,
        "credentials": [{"type": "password", "value": "owner-pw", "temporary": False}]})
    subjects[email] = root.request(f"/admin/realms/{R}/users?username={username}&exact=true")[0]["id"]
json.dump({"alias": ALIAS, "callback": site["callback"], "member_secret": secret,
           "subjects": subjects, "lines": LINES}, open("/tmp/lab-facts.json", "w"))
print("keycloak ready:", ALIAS, site["callback"])
