"""Shared account transport. Tenant Sites never receive identity admin credentials.

The private account endpoint reuses the already provisioned tenant/product HMAC
credential and internal address, but it is not a billing method or generic proxy.
"""
import uuid

import frappe
import requests
from frappe import _

from pearnly_core.account import line_login_contract

METHOD = "pearnly_control.account.member_api.execute"
TIMEOUT = 45


def _post(payload, products):
    config = {key: str(frappe.conf.get("pearnly_" + key) or "") for key in (
        "tenant", "product", "billing_api", "billing_host_header", "billing_token")}
    # 各产品各自一个账号池（ERP / Cowork 分池，用户 2026-09-26）；控制面按本站签发方选池，没带时按令牌里的产品。
    if config["product"] not in products or not all(config.values()):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    payload = {"tenant": config["tenant"], **payload}
    headers = {"Host": config["billing_host_header"],
        "X-Pearnly-Billing-Product": config["product"],
        "X-Pearnly-Billing-Token": config["billing_token"]}
    result = None
    # One retry, with an unchanged reset id. Server ownership and durable reset
    # intent make lost responses recoverable; never log requests or exceptions.
    for _attempt in range(2):
        try:
            response = requests.post(config["billing_api"].rstrip("/") + "/api/method/" + METHOD,
                json=payload, headers=headers, timeout=TIMEOUT, allow_redirects=False)
            if response.status_code == 200:
                result = response.json().get("message")
                if isinstance(result, dict) and result.get("error") != "unavailable":
                    break
        except (requests.RequestException, ValueError, AttributeError):
            result = None
    if not isinstance(result, dict) or not result.get("ok"):
        code = result.get("error") if isinstance(result, dict) else "unavailable"
        if code == "conflict":
            frappe.throw(_("Already exists"))
        if code in ("not_permitted", "invalid_request"):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        frappe.throw(_("Please try again later"))
    return result


def _issuer():
    # 控制面按本站实际签到的账号池建号、改密码、写关联（老租户可能与产品默认的池不同）。
    return str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/")


def _call(action, *, email, password, username=""):
    result = _post({"action": action, "email": email, "password": password, "username": username,
                    "request_id": uuid.uuid4().hex, "issuer": _issuer()}, ("erp", "cowork"))
    if result.get("email") != email or (action == "create" and not result.get("subject")):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return result


def link_line(subject, line_user_id, login_channel):
    """把本站生效的 LINE 绑定登记到控制面：该 LINE 在本站账号池的专用身份挂上这个 LINE。"""
    result = _post({"action": "link-line", "subject": subject, "line_user_id": line_user_id,
                    "login_channel": login_channel, "issuer": _issuer()}, line_login_contract.PRODUCTS)
    if result.get("subject") != subject:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def unlink_line(subject, login_channel):
    result = _post({"action": "unlink-line", "subject": subject, "login_channel": login_channel,
                    "issuer": _issuer()}, line_login_contract.PRODUCTS)
    if result.get("subject") != subject:
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def create(email, username, password):
    return _call("create", email=email, username=username, password=password)["subject"]


def reset_password(email, password):
    return _call("reset-password", email=email, password=password)
