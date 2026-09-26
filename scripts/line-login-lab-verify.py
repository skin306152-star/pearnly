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
OWNER, STRANGER = "owner@lab.test", "stranger@lab.test"
SITE = "http://erp.lab:8000"
KC = "http://keycloak:8080"
LINE_UA = "Mozilla/5.0 (iPhone) AppleWebKit Mobile Safari Line/14.9.0"
m = runpy.run_path("/lab/scripts/production/configure-line-login.py")
kc = m["Keycloak"](KC, "master", "lab-admin", "lab-admin-only")
R = "/admin/realms/pearnly-business"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def db(fn):
    frappe.init(site="erp.lab", sites_path=".")
    frappe.connect()
    try:
        out = fn()
        frappe.db.commit()
        return out
    finally:
        frappe.destroy()


def bind(user, line_user_id):
    from pearnly_core import line_binding

    code = db(lambda: line_binding.generate_bind_code(user)["code"])
    owner = db(lambda: line_binding.consume_bind_code(code, line_user_id=line_user_id))["user"]
    return db(lambda: line_binding.bind(line_user_id, owner, "Lab LINE"))


def binding_state(line_user_id):
    return db(lambda: frappe.db.get_value("Pearnly LINE Binding", {"line_user_id": line_user_id},
                                          ["user", "status", "login_link"], as_dict=True))


def wait_link(line_user_id, want, seconds=40):
    for _ in range(seconds * 2):
        row = binding_state(line_user_id)
        if row and row.login_link == want:
            return row
        time.sleep(0.5)
    return binding_state(line_user_id)


def kc_links(email):
    return [(x["identityProvider"], x["userId"]) for x in kc.request(f"{R}/users/{SUBJECTS[email]}/federated-identity")]


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
            if stop_at_site and loc.startswith(SITE) and "/api/method/pearnly_core" not in loc:
                return r, loc
            r = self.s.get(loc, allow_redirects=False)
            self.trail.append((r.status_code, loc))
            for c in self.s.cookies:
                c.secure = False
        return r, None

    def submit(self, r, **fields):
        action = html.unescape(re.search(r'action="([^"]+)"', r.text).group(1))
        return self.go("POST", action, stop_at_site=True, data=fields)

    def user(self):
        r = self.s.get(SITE + "/api/method/frappe.auth.get_logged_user")
        return r.json().get("message") if r.status_code == 200 else f"HTTP {r.status_code}"

    def enter(self, to="/desk/lab-target"):
        return self.go("GET", f"{SITE}/api/method/pearnly_core.account.line_login.enter?to={to}", stop_at_site=True)


def line_login(line_user, to="/desk/lab-target"):
    b = Browser(LINE_UA)
    r, landed = b.enter(to)
    if landed:
        return b, r, landed
    if 'name="username"' not in r.text:
        return b, r, None
    r, landed = b.submit(r, username=line_user, password="line-pw")
    return b, r, landed


def sessions(user):
    return db(lambda: frappe.get_all("Pearnly Session", filters={"user": user, "status": "active"},
                                     fields=["session_id", "identity_provider", "line_user_id"]))


# ── A. binding links the LINE in Keycloak through the member service ─────────
bind(OWNER, U1)
row = wait_link(U1, "linked")
check("A1 binding → login_link=linked (worker, member service)", row and row.login_link == "linked", row)
check("A2 Keycloak federated link owner ↔ U1", (ALIAS, U1) in kc_links(OWNER), kc_links(OWNER))

# ── B. LINE in-app browser: tap link → no password → lands on target ──────────
b_line, r, landed = line_login("lineuser1")
check("B1 LINE entry lands on the deep link", landed == SITE + "/desk/lab-target", landed or r.status_code)
check("B2 site session is the owner", b_line.user() == OWNER, b_line.user())
rows = sessions(OWNER)
check("B3 session register records LINE source",
      any(s.identity_provider == ALIAS and s.line_user_id == U1 for s in rows), rows)
check("B4 went straight to the hidden LINE entry, no Pearnly password form",
      any("kc_idp_hint=" + ALIAS in u for _, u in b_line.trail)
      and any(f"/broker/{ALIAS}/login" in u for _, u in b_line.trail)
      and not any("/realms/pearnly-business/login-actions/authenticate" in u for _, u in b_line.trail),
      [u[:70] for _, u in b_line.trail[:4]])

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

# ── E. unbind → LINE session dies at once, password session survives, link removed
db(lambda: __import__("pearnly_core.line_binding", fromlist=["x"]).unbind(U1))
check("E1 LINE session revoked immediately", b_line.user() in ("Guest", "HTTP 403", "HTTP 401"), b_line.user())
check("E2 password session untouched", b_pw.user() == OWNER, b_pw.user())
row = wait_link(U1, "unlinked")
check("E3 login_link=unlinked and Keycloak link removed",
      row.login_link == "unlinked" and (ALIAS, U1) not in kc_links(OWNER), (row, kc_links(OWNER)))

# ── F. unbound LINE → Keycloak refuses, no account is created ────────────────
users_before = len(kc.request(f"{R}/users"))
b, r, landed = line_login("lineuser1")
check("F1 unlinked LINE refused by Keycloak", landed is None and r.status_code in (400, 401, 403), r.status_code)
check("F2 no Keycloak account created", len(kc.request(f"{R}/users")) == users_before)

# ── G. stale Keycloak link but no binding on the site → site refuses, SSO ended ─
kc.request(f"{R}/users/{SUBJECTS[OWNER]}/federated-identity/{ALIAS}", "POST",
           {"identityProvider": ALIAS, "userId": U1, "userName": U1})
kc_before = len(kc.request(f"{R}/users/{SUBJECTS[OWNER]}/sessions"))
b, r, landed = line_login("lineuser1")
check("G1 site refuses a LINE login without an active binding", b.user() in ("Guest", "HTTP 403"), b.user())
check("G2 refusal went through Keycloak end-session", any("logout" in u for _, u in b.trail), [u[:80] for _, u in b.trail[-3:]])
kc_sessions = kc.request(f"{R}/users/{SUBJECTS[OWNER]}/sessions")
check("G3 the refused LINE login left no Keycloak session behind",
      len(kc_sessions) == kc_before, (kc_before, len(kc_sessions)))
kc.request(f"{R}/users/{SUBJECTS[OWNER]}/federated-identity/{ALIAS}", "DELETE")

# ── H. re-bind, then the same LINE moves to another person of the tenant ──────
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
check("H2 link moved: stranger holds U1, owner does not",
      row.user == STRANGER and (ALIAS, U1) in kc_links(STRANGER) and (ALIAS, U1) not in kc_links(OWNER),
      (row, kc_links(STRANGER), kc_links(OWNER)))
b, r, landed = line_login("lineuser1")
check("H3 LINE now signs in as the new holder", b.user() == STRANGER, b.user())

# ── I. hourly reconcile repairs a lost link ───────────────────────────────────
kc.request(f"{R}/users/{SUBJECTS[STRANGER]}/federated-identity/{ALIAS}", "DELETE")
db(lambda: frappe.db.set_value("Pearnly LINE Binding", {"line_user_id": U1}, "login_link", "failed"))
db(lambda: __import__("pearnly_core.account.line_login", fromlist=["x"]).reconcile())
check("I1 reconcile relinks a failed binding", (ALIAS, U1) in kc_links(STRANGER)
      and binding_state(U1).login_link == "linked", (kc_links(STRANGER), binding_state(U1)))

# ── J. second LINE for an unregistered identity is refused by the member service
bind(OWNER, U2)
row = wait_link(U2, "linked", seconds=15)
check("J1 owner (registered) can hold a different LINE", row.login_link == "linked", row)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
