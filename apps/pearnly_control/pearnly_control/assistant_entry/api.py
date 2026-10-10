"""智能体入口站点的接口（ADR-019）。都是访客接口：入口页本来就在登录之前；只做解析、限流后交给 `entry`。"""
import frappe
from frappe.rate_limiter import rate_limit

from pearnly_control.assistant_entry import contracts, entry


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60)
def start(login):
    try:
        return {"url": entry.start(login)}
    except contracts.ContractError:
        frappe.throw(frappe._("Enter your username or email."))


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=60, seconds=60)
def callback(code=None, state=None, error=None, **_ignored):
    entry.callback(code=code, state=state, error=error)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def choose(pick, site):
    try:
        entry.choose(pick, site)
    except contracts.ContractError:
        frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
