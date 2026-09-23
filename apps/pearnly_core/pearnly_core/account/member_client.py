"""Shared account transport. Tenant Sites never receive identity admin credentials.

The private account endpoint reuses the already provisioned tenant/product HMAC
credential and internal address, but it is not a billing method or generic proxy.
"""
import uuid

import frappe
import requests
from frappe import _

METHOD = "pearnly_control.account.member_api.execute"
TIMEOUT = 45


def _call(action, *, email, password, username=""):
    config = {key: str(frappe.conf.get("pearnly_" + key) or "") for key in (
        "tenant", "product", "billing_api", "billing_host_header", "billing_token")}
    if config["product"] != "erp" or not all(config.values()):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    request_id = uuid.uuid4().hex
    payload = {"tenant": config["tenant"], "action": action, "email": email,
        "password": password, "username": username, "request_id": request_id}
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
    if result.get("email") != email or (action == "create" and not result.get("subject")):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return result


def create(email, username, password):
    return _call("create", email=email, username=username, password=password)["subject"]


def reset_password(email, password):
    return _call("reset-password", email=email, password=password)
