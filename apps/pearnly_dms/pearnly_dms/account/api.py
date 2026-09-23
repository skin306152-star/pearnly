"""Menus 3/4: authenticated own-credential maintenance and one-use portal handoff.

Source: ec2e5c957e6b self_credentials / line_dms_portal_routes. Native Site
encryption and binding revisions replace old tenant JWT/database ticket storage.
"""
import secrets
from urllib.parse import urlencode

import frappe
from werkzeug.wrappers import Response

from pearnly_dms.editor import browser_api, browser_ticket
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.account import portal
from pearnly_core.endpoints import read_password

PORTAL_PREFIX = "pearnly:dms:portal:"


def _identity(ticket):
    who = browser_ticket.resolve(ticket)
    if not who or who["mode"] != "account":
        browser_api._fail("dms_booking.line_auth_required", 401)
    return who


def _endpoint(who):
    resolved = resolve_dms_endpoint(who["user"], None)
    if not resolved:
        browser_api._fail("dms_booking.no_endpoint", 409)
    return frappe.get_doc("Pearnly ERP Endpoint", resolved["name"])


@frappe.whitelist(allow_guest=True, methods=["POST"])
def authenticate(id_token: str = ""):
    line_user = browser_api._line_user_from_request(id_token)
    return {"ticket": browser_ticket.issue(line_user, "account"),
            "language": browser_api._bound_language(line_user)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def credentials(ticket: str):
    endpoint = _endpoint(_identity(ticket))
    return {"username": endpoint.username or ""}  # Stored passwords never leave this API.


@frappe.whitelist(allow_guest=True, methods=["POST"])
def save_credentials(ticket: str, password: str, username: str = ""):
    """改自己的 DMS 凭据 —— **只改密码**。

    用户名（DMS 登录名）由后台发号、绑死这个租户，界面不给改、这里也不收改动
    （用户 2026-09-23："可以用用户名登录，但是不能修改…用户只可以自己改密码"）。
    老客户端仍会带上 `username`：与站上记录一致就照旧通过，**想改就明确拒掉**，
    不静默忽略（静默会让用户以为改成了）。
    """
    who = _identity(ticket)
    password = str(password or "")
    if not password or len(password) > 256:
        browser_api._fail("dms.account.invalid", 400)
    if any(ch in password for ch in ("\r", "\n", "\x00")):
        browser_api._fail("dms.account.invalid", 400)
    _endpoint(who)
    # Lock the native binding through the update so concurrent unbind cannot
    # authorize a completed credentials change using a stale cached ticket.
    frappe.db.sql("select name from `tabPearnly LINE Binding` where line_user_id=%s for update",
                  (who["line_user_id"],))
    _identity(ticket)
    name = frappe.db.get_value("DMS Operator", {"user": who["user"], "status": "active"}, "name")
    if not name:
        browser_api._fail("dms_booking.not_bound", 403)
    doc = frappe.get_doc("DMS Operator", name, for_update=True)
    if str(username or "").strip() and str(username).strip() != (doc.dms_username or ""):
        browser_api._fail("dms.account.usernameFixed", 409)
    doc.dms_password = password
    doc.save(ignore_permissions=True)  # Native on_update synchronizes the owned endpoint.
    return {"ok": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def portal_ticket(ticket: str, language: str = ""):
    from pearnly_dms.i18n import LANGUAGES
    who = _identity(ticket)
    _endpoint(who)
    token = secrets.token_urlsafe(32)
    frappe.cache().set_value(PORTAL_PREFIX + token, {"account_ticket": ticket,
        "language": language if language in LANGUAGES else browser_api._bound_language(who["line_user_id"])},
        expires_in_sec=60)
    return {"url": "/api/method/pearnly_dms.account.api.open_portal?" + urlencode({"token": token}),
            "expires_in": 60}


@frappe.whitelist(allow_guest=True)
def open_portal(token: str):
    key = PORTAL_PREFIX + str(token or "")
    with frappe.cache().lock(key + ":lock", timeout=5, blocking_timeout=2):
        record = frappe.cache().get_value(key)
        frappe.cache().delete_value(key)
    if not isinstance(record, dict):
        browser_api._fail("dms_booking.expired", 409)
    who = _identity(record["account_ticket"])
    endpoint = _endpoint(who)
    # 凭据解不开（密钥换过/密文损坏）= 这个门户现在用不了：回一句"没配连接"，
    # 而不是把框架的加密报错抖给用户。
    password = read_password("Pearnly ERP Endpoint", endpoint.name, "password")
    if not password:
        browser_api._fail("dms_booking.no_endpoint", 409)
    page, headers = portal.render(endpoint.system_url, endpoint.username,
                                  password,
                                  record.get("language") or browser_api._bound_language(who["line_user_id"]))
    return Response(page, mimetype="text/html", headers=headers)
