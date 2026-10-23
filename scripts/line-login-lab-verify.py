"""LINE sign-in lab, step 3: end-to-end checks on real Frappe sites, real Keycloak, fake LINE provider.

Exit status is non-zero when any check fails.
"""
import html
import json
import os
import sys

# Laboratory only: refuse anything but the two synthetic lab sites and the lab Keycloak.
if os.environ.get("PEARNLY_LINE_LOGIN_LAB") != "1":
    raise SystemExit("Run through scripts/line-login-lab.sh inside the lab bench only")
import re
import runpy
import time
from urllib.parse import parse_qs, urlparse

import requests

os.chdir("/home/frappe/frappe-bench/sites")
import frappe  # noqa: E402

facts = json.load(open("/tmp/lab-facts.json"))
ALIAS, LINES, SUBJECTS = facts["alias"], facts["lines"], facts["subjects"]
U1, U2 = LINES["lineuser1"], LINES["lineuser2"]
OWNER, STRANGER, OTHER = "owner@lab.test", "stranger@lab.test", "other@lab.test"
SITE = "http://erp.lab:8000"
SITE2 = "http://erp2.lab:8000"
KC = "http://keycloak:8080"
# LINE 里的网页都经入口先转进 LIFF（2026-10-23），免密登录发生在 LIFF 里。
LINE_UA = "Mozilla/5.0 (iPhone) AppleWebKit Mobile Safari Line/14.9.0 LIFF"
m = runpy.run_path("/lab/scripts/production/configure-line-login.py")
kc = m["Keycloak"](KC, "master", "lab-admin", "lab-admin-only")
R = "/admin/realms/pearnly-business"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def db(fn, site="erp.lab"):
    frappe.init(site=site, sites_path=".")
    frappe.connect()
    try:
        out = fn()
        frappe.db.commit()
        return out
    finally:
        frappe.destroy()


def bind(user, line_user_id, site="erp.lab"):
    from pearnly_core import line_binding

    code = db(lambda: line_binding.generate_bind_code(user)["code"], site)
    owner = db(lambda: line_binding.consume_bind_code(code, line_user_id=line_user_id), site)["user"]
    return db(lambda: line_binding.bind(line_user_id, owner, "Lab LINE"), site)


def binding_state(line_user_id, site="erp.lab"):
    return db(lambda: frappe.db.get_value("Pearnly LINE Binding", {"line_user_id": line_user_id},
                                          ["user", "status", "login_link"], as_dict=True), site)


def wait_link(line_user_id, want, seconds=40, site="erp.lab"):
    for _ in range(seconds * 2):
        row = binding_state(line_user_id, site)
        if row and row.login_link == want:
            return row
        time.sleep(0.5)
    return binding_state(line_user_id, site)


def kc_links(email):
    return [(x["identityProvider"], x["userId"]) for x in kc.request(f"{R}/users/{SUBJECTS[email]}/federated-identity")]


def holders(line_user_id):
    """Who holds this LINE at the shared entry: must be its LINE person, never a tenant account."""
    return kc.request(f"{R}/users?idpAlias={ALIAS}&idpUserId={line_user_id}&briefRepresentation=true") or []


def held_by_person(line_user_id):
    rows = holders(line_user_id)
    return len(rows) == 1 and rows[0]["username"].startswith("pearnly-line-")


def tenant_accounts_hold_no_line():
    return all(not kc_links(email) for email in (OWNER, STRANGER, OTHER))


class Browser:
    def __init__(self, ua="Mozilla/5.0 Safari"):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = ua
        self.trail = []

    def go(self, method, url, stop_at_site=False, **kw):
        r = self.s.request(method, url, allow_redirects=False, **kw)
        self.trail.append((r.status_code, url))
        for c in self.s.cookies:
            c.secure = False
        while r.status_code in (301, 302, 303):
            loc = r.headers["Location"]
            if loc.startswith("/"):
                base = urlparse(r.url)
                loc = f"{base.scheme}://{base.netloc}{loc}"
            if stop_at_site and loc.startswith((SITE, SITE2)) and "/api/method/pearnly_core" not in loc:
                return r, loc
            r = self.s.get(loc, allow_redirects=False)
            self.trail.append((r.status_code, loc))
            for c in self.s.cookies:
                c.secure = False
        return r, None

    def submit(self, r, **fields):
        action = html.unescape(re.search(r'action="([^"]+)"', r.text).group(1))
        return self.go("POST", action, stop_at_site=True, data=fields)

    def user(self, site=SITE):
        r = self.s.get(site + "/api/method/frappe.auth.get_logged_user")
        return r.json().get("message") if r.status_code == 200 else f"HTTP {r.status_code}"

    def enter(self, to="/desk/lab-target", site=SITE):
        return self.go("GET", f"{site}/api/method/pearnly_core.account.line_login.enter?to={to}", stop_at_site=True)


def line_login(line_user, to="/desk/lab-target", site=SITE):
    b = Browser(LINE_UA)
    r, landed = b.enter(to, site)
    if landed:
        return b, r, landed
    if 'name="username"' not in r.text:
        return b, r, None
    r, landed = b.submit(r, username=line_user, password="line-pw")
    return b, r, landed


def sessions(user):
    return db(lambda: frappe.get_all("Pearnly Session", filters={"user": user, "status": "active"},
                                     fields=["session_id", "identity_provider", "line_user_id"]))


# ── A. binding: the LINE's own person in Keycloak holds it, not the tenant account ──
bind(OWNER, U1)
row = wait_link(U1, "linked")
check("A1 binding → login_link=linked (worker, member service)", row and row.login_link == "linked", row)
check("A2 the LINE person holds U1; tenant accounts hold no LINE",
      held_by_person(U1) and tenant_accounts_hold_no_line(), (holders(U1), kc_links(OWNER)))
person = kc.request(f"{R}/users/{holders(U1)[0]['id']}")
check("A3 the LINE person has no password, a placeholder email and no pending profile form",
      not kc.request(f"{R}/users/{person['id']}/credentials") and person["email"].endswith("@line.invalid")
      and not person.get("requiredActions"), person)

# ── B. LINE in-app browser: tap link → no password → lands on target as the bound user ─
b_line, r, landed = line_login("lineuser1")
check("B1 LINE entry lands on the deep link", landed == SITE + "/desk/lab-target", landed or r.status_code)
check("B2 site session is the bound user", b_line.user() == OWNER, b_line.user())
rows = sessions(OWNER)
check("B3 session register records LINE source",
      any(s.identity_provider == ALIAS and s.line_user_id == U1 for s in rows), rows)
check("B4 went straight to the hidden LINE entry, no Pearnly password form",
      any("kc_idp_hint=" + ALIAS in u for _, u in b_line.trail)
      and any(f"/broker/{ALIAS}/login" in u for _, u in b_line.trail)
      and not any("/realms/pearnly-business/login-actions/authenticate" in u for _, u in b_line.trail),
      [u[:70] for _, u in b_line.trail[:4]])
local_name = db(lambda: frappe.db.get_value("User", OWNER, "username"))
check("B5 the LINE person's username never overwrites the local user", not str(local_name).startswith("pearnly-line-"),
      local_name)

# ── C. same link outside LINE → no LINE hop ───────────────────────────────────
b = Browser()
r, landed = b.enter()
check("C1 other browsers go straight to the target (normal login from there)",
      landed == SITE + "/desk/lab-target" and not any("keycloak" in u for _, u in b.trail), b.trail)

# ── D. password login still works and is not marked as LINE ──────────────────
b_pw = Browser()
r, _ = b_pw.go("GET", SITE + "/api/method/pearnly_core.identity.begin_login?redirect_to=/desk/pw-target")
check("D0 password login page has no LINE button", "line-erp" not in r.text and ">LINE<" not in r.text)
r, landed = b_pw.submit(r, username="LabOwner", password="owner-pw")
check("D1 password login lands", b_pw.user() == OWNER, b_pw.user())
rows = sessions(OWNER)
check("D2 password session has no LINE source", sum(1 for s in rows if not s.identity_provider) >= 1, rows)

# ── E. unbind → LINE session dies at once, password session survives, the person is gone
db(lambda: __import__("pearnly_core.line_binding", fromlist=["x"]).unbind(U1))
check("E1 LINE session revoked immediately", b_line.user() in ("Guest", "HTTP 403", "HTTP 401"), b_line.user())
check("E2 password session untouched", b_pw.user() == OWNER, b_pw.user())
row = wait_link(U1, "unlinked")
check("E3 login_link=unlinked and the LINE person removed", row.login_link == "unlinked" and not holders(U1),
      (row, holders(U1)))

# ── F. unbound LINE → Keycloak refuses, no account is created ────────────────
users_before = len(kc.request(f"{R}/users"))
b, r, landed = line_login("lineuser1")
check("F1 unlinked LINE refused by Keycloak", landed is None and r.status_code in (400, 401, 403), r.status_code)
check("F2 no Keycloak account created", len(kc.request(f"{R}/users")) == users_before)

# ── G. a person still holds the LINE but the site has no binding → site refuses, explains,
#       and its sign-in button ends the identity session (no SSO loop back into a refusal) ──
kc.request(f"{R}/users", "POST", {"username": "lab-stale-person", "email": "stale@line.invalid", "enabled": True,
                                  "emailVerified": True})
stale = kc.request(f"{R}/users?username=lab-stale-person&exact=true")[0]["id"]
kc.request(f"{R}/users/{stale}/federated-identity/{ALIAS}", "POST",
           {"identityProvider": ALIAS, "userId": U1, "userName": U1})
b, r, landed = line_login("lineuser1")
check("G1 site refuses a LINE login without an active binding", b.user() in ("Guest", "HTTP 403"), b.user())
logout = re.search(r"""href=["']([^"']*logout[^"']*)["']""", html.unescape(r.text or ""))
check("G2 refusal page explains and offers sign-in through end-session",
      r.status_code == 403 and logout is not None and "not bound" in (r.text or ""), (r.status_code, (r.text or "")[:200]))
check("G3 the refused LINE login is still a live identity session until the button", len(
      kc.request(f"{R}/users/{stale}/sessions")) == 1)
if logout:
    b.go("GET", logout.group(1))
check("G4 the sign-in button ended the identity session", not kc.request(f"{R}/users/{stale}/sessions"))
kc.request(f"{R}/users/{stale}", "DELETE")

# ── H. re-bind, then the same LINE moves to another person of the same tenant ─
bind(OWNER, U1)
wait_link(U1, "linked")
b_owner_line, r, landed = line_login("lineuser1")
check("H0 owner LINE login again after re-binding", b_owner_line.user() == OWNER, b_owner_line.user())
# stranger is registered to the tenant in the billing registry for this step
frappe.init(site="billing.lab", sites_path=".")
frappe.connect()
__import__("pearnly_control.account.member_service", fromlist=["x"]).register_owner("T-2026-0001", SUBJECTS[STRANGER])
frappe.db.commit()
frappe.destroy()
bind(STRANGER, U1)
check("H1 owner's LINE session revoked when the LINE moved", b_owner_line.user() != OWNER, b_owner_line.user())
row = wait_link(U1, "linked")
check("H2 binding moved; still one LINE person, no tenant account holds it",
      row.user == STRANGER and held_by_person(U1) and tenant_accounts_hold_no_line(), (row, holders(U1)))
b, r, landed = line_login("lineuser1")
check("H3 LINE now signs in as the new holder", b.user() == STRANGER, b.user())

# ── I. hourly reconcile repairs a lost link ───────────────────────────────────
kc.request(f"{R}/users/{holders(U1)[0]['id']}", "DELETE")
db(lambda: frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": U1}, "login_link", "failed"))
db(lambda: __import__("pearnly_core.account.line_login", fromlist=["x"]).reconcile())
check("I1 reconcile recreates the person for a failed binding", held_by_person(U1)
      and binding_state(U1).login_link == "linked", (holders(U1), binding_state(U1)))

# ── M. the 2026-10-04 case: one LINE, two tenants, two different accounts ─────
bind(OTHER, U1, site="erp2.lab")
row = wait_link(U1, "linked", site="erp2.lab")
check("M1 the second tenant's own account binds the same LINE", row and row.login_link == "linked", row)
check("M2 still exactly one LINE person; no tenant account holds it", held_by_person(U1) and tenant_accounts_hold_no_line(),
      (holders(U1), kc_links(STRANGER), kc_links(OTHER)))
b, r, landed = line_login("lineuser1", site=SITE2)
check("M3 LINE opens the second site as that site's account", b.user(SITE2) == OTHER, b.user(SITE2))
b, r, landed = line_login("lineuser1", site=SITE)
check("M4 and still opens the first site as its own account", b.user(SITE) == STRANGER, b.user(SITE))
db(lambda: __import__("pearnly_core.line_binding", fromlist=["x"]).unbind(U1), "erp2.lab")
wait_link(U1, "unlinked", site="erp2.lab")
check("M5 unbinding one tenant keeps the person for the other", held_by_person(U1), holders(U1))
b, r, landed = line_login("lineuser1", site=SITE)
check("M6 the first site still opens by LINE", b.user(SITE) == STRANGER, b.user(SITE))

# ── J. a registered account can hold a different LINE ─────────────────────────
bind(OWNER, U2)
row = wait_link(U2, "linked", seconds=15)
check("J1 owner (registered) can hold a different LINE", row.login_link == "linked" and held_by_person(U2), row)

# ── L. one shared entry: the same account binds the same LINE on two sites ────
bind(OWNER, U2, site="erp2.lab")
row = wait_link(U2, "linked", site="erp2.lab")
check("L1 second site links the same LINE through the shared entry", row and row.login_link == "linked", row)
b, r, landed = line_login("lineuser2", site=SITE2)
check("L2 LINE opens the second site without a password", b.user(SITE2) == OWNER, b.user(SITE2))
db(lambda: __import__("pearnly_core.line_binding", fromlist=["x"]).unbind(U2))  # unbind on the first site only
wait_link(U2, "unlinked")
check("L3 unbinding on one site keeps the LINE person for the other", held_by_person(U2), holders(U2))
b, r, landed = line_login("lineuser2", site=SITE2)
check("L4 second site still opens by LINE", b.user(SITE2) == OWNER, b.user(SITE2))
b, r, landed = line_login("lineuser2", site=SITE)
check("L5 first site refuses the same LINE now it is unbound there", b.user(SITE) != OWNER, b.user(SITE))
db(lambda: __import__("pearnly_core.line_binding", fromlist=["x"]).unbind(U2), "erp2.lab")
wait_link(U2, "unlinked", site="erp2.lab")
check("L6 unbinding on the last site removes the LINE person", not holders(U2), holders(U2))

# ── K. only credential actions demand a fresh password; profile/locale keep Keycloak defaults ─
ages = {a: ((kc.request(f"{R}/authentication/required-actions/{a}/config", ok=(200, 400, 404)) or {})
            .get("config") or {}).get("max_auth_age") for a in ("UPDATE_PASSWORD", "CONFIGURE_TOTP",
                                                                "UPDATE_PROFILE", "update_user_locale")}
check("K1 credential actions re-authenticate after 1 s, profile/locale untouched",
      ages["UPDATE_PASSWORD"] == "1" and ages["CONFIGURE_TOTP"] == "1"
      and ages["UPDATE_PROFILE"] != "1" and ages["update_user_locale"] != "1", ages)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
