"""Real local DB + simulated LINE menu contract; never logs or changes external credentials."""
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab-b.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_core import line_binding
from pearnly_dms.account import api
from pearnly_dms.connections import api as connections
from pearnly_dms.editor import browser_ticket
from pearnly_dms.menu import rich_menu
from pearnly_dms.operators import roster
from pearnly_core import line_session
from pearnly_dms.line import identity, out

def check(value, label):
    assert value, label
    print("PASS", label)

try:
    check(frappe.conf.pearnly_line_channel_access_token == "lab:" + frappe.local.site, "isolated LINE destination")
    connections.save_connection("lab-owner", "lab-only", "https://example.test/dms/index.php")
    name = frappe.db.get_value("DMS Operator", {"display_name": "Account contract fixture"}, "name")
    row = roster._row(frappe.get_doc("DMS Operator", name)) if name else roster.create_operator(
        "Account contract fixture", "lab-sales", "lab-initial", can_query_dms=0)
    roster.update_operator(row["name"], can_query_dms=0)
    user = "Ulab-account-contract"
    line_binding.bind(user, row["user"], "Test only")
    ticket = browser_ticket.issue(user, "account")
    check(api.credentials(ticket).keys() == {"username"}, "credential response never returns password")
    # 用户名开户时定死、不能改（用户 2026-09-23："可以用用户名登录，但是不能修改…用户
    # 只可以自己改密码"）：想改必须被拒，密码照改。老客户端仍会带上同名参数，见下面一条。
    # 存密码前会用新密码真试登录一次；本实验室的 DMS 是 example.test，登不上 —— 这里只把那次
    # 试登录替身成"登上了"/"被拒"，其余照真实站点走。
    probe = "pearnly_dms.adapter.account_probe.probe"
    with patch(probe, return_value={"ok": True, "missing": []}) as signed_in:
        api.save_credentials(ticket, "lab-updated-only")
    check(signed_in.call_args.args[1:] == ("lab-sales", "lab-updated-only", "login"),
          "password is tried once against the operator's own DMS login")
    check(api.credentials(ticket) == {"username": "lab-sales"}, "own stored username unchanged after password update")
    try:
        api.save_credentials(ticket, "lab-updated-only", username="lab-sales-updated")
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("renaming the DMS username must be refused")
    check(api.credentials(ticket) == {"username": "lab-sales"}, "refused rename leaves the username alone")
    endpoint = api._endpoint(api._identity(ticket))
    check(endpoint.get_password("password") == "lab-updated-only", "adapter receives current encrypted credential")
    with patch(probe, return_value={"ok": False, "error_code": "ERR_DMS_AUTH"}):
        try:
            api.save_credentials(ticket, "lab-typo")
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("a password DMS refuses must not be stored")
    endpoint = api._endpoint(api._identity(ticket))
    check(endpoint.get_password("password") == "lab-updated-only", "refused password leaves the working one stored")
    portal = api.portal_ticket(ticket, language="zh")
    token = parse_qs(urlsplit(portal["url"]).query)["token"][0]
    response = api.open_portal(token)
    check(response.status_code == 200 and 'https://example.test/dms/login/checklogin.php' in response.get_data(as_text=True), "portal targets configured DMS")
    check("使用本人的账号进入 DMS" in response.get_data(as_text=True), "portal handoff follows selected UI language")
    check(response.headers["Cache-Control"].startswith("no-store"), "portal credentials cannot be cached")
    try:
        api.open_portal(token)
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("portal token replay")
    print("PASS one-use portal token rejects replay")
    menus = rich_menu.publish()
    rich_menu.sync(user)
    current = lambda: rich_menu._request("GET", "user/" + user + "/richmenu", missing_ok=True).get("richMenuId")
    check(current() == menus["basic"], "ordinary operator receives four-item menu")
    roster.update_operator(row["name"], can_query_dms=1)
    frappe.db.commit()
    rich_menu.sync(user)
    check(current() == menus["query"], "query grant receives five-item menu")
    roster.update_operator(row["name"], can_query_dms=0)
    frappe.db.commit()
    rich_menu.sync(user)
    check(current() == menus["basic"], "query revocation restores four-item menu")
    queued = {"binding": {"user_id": row["user"], "binding_revision": identity.current(user)["binding_revision"]},
              "line_user_id": user}
    line_session.set_session(user, "reviewing", {"nonce": "pre-unbind-fixture"})
    line_binding.unbind(user)
    check(line_session.get_session(user) is None, "unbind discards old draft")
    check(out.run_guarded("pearnly_dms.nonexistent_stale_job", queued) == {"cancelled": "binding_changed"},
          "revoked queued work never resolves or calls its target")
    check(browser_ticket.resolve(ticket) is None, "unbind immediately invalidates account ticket")
    line_binding.bind(user, row["user"])
    check(browser_ticket.resolve(ticket) is None, "rebind cannot resurrect old ticket")
    check(out.run_guarded("pearnly_dms.nonexistent_stale_job", queued) == {"cancelled": "binding_changed"},
          "rebind to same employee cannot revive queued work")
    fresh = browser_ticket.issue(user, "account")
    replacement = user + "-replacement"
    line_binding.bind(replacement, row["user"])
    check(line_binding.binding_of(user) is None and browser_ticket.resolve(fresh) is None,
          "binding a new LINE account revokes the previous account and ticket")
    line_binding.bind(user, row["user"])
    check(line_binding.binding_of(replacement) is None, "employee has only one active LINE binding")
    fresh = browser_ticket.issue(user, "account")
    roster.set_status(row["name"], "inactive")
    check(browser_ticket.resolve(fresh) is None, "inactive operator cannot reuse ticket")
    roster.set_status(row["name"], "active")
    line_binding.unbind(user)
    rich_menu.sync(user)
    check(current() is None, "unbind removes per-user menu override")
    frappe.db.commit()
finally:
    frappe.destroy()

# Publish only into the simulator for the interactive real-DMS test operator.
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
try:
    assert frappe.conf.pearnly_line_channel_access_token == "lab:" + frappe.local.site
    rich_menu.publish()
    frappe.db.commit()
    rich_menu.sync("Ulab-alice")
    print("PASS interactive laboratory menu published; production untouched")
finally:
    frappe.destroy()
