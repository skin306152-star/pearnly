"""计费服务给智能体入口站点的内部查询接口（ADR-019）。

只在计费站点上可用，走 compose 内网，用专用令牌（由计费服务根密钥派生，见 `contracts.entry_token`）：
与产品站点的计费令牌、管理令牌都分开，拿到它也只能做这两种只读查询。
"""
import secrets

import frappe
from frappe import _

from pearnly_control.assistant_entry import contracts, registry

HEADER = "X-Pearnly-Assistant-Entry"


def _authorize():
    if not frappe.conf.get("pearnly_billing_only"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    from pearnly_control import billing_api

    root = billing_api.service_token(create=False)
    actual = frappe.get_request_header(HEADER) or ""
    if not root or not actual or not secrets.compare_digest(contracts.entry_token(root), actual):
        frappe.throw(_("Invalid service token"), frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resolve(action: str, payload=None):
    _authorize()
    payload = frappe.parse_json(payload) or {}
    try:
        if action == "issuers":
            return {"issuers": registry.issuers_for(payload.get("login"))}
        if action == "sites":
            subject = str(payload.get("subject") or "").strip()
            if not subject:
                raise contracts.ContractError("missing subject")
            return {"sites": registry.sites_for(payload.get("issuer"), subject, str(payload.get("username") or ""))}
    except contracts.ContractError:
        pass
    frappe.throw(_("Not permitted"), frappe.PermissionError)
