"""智能体工具：声明、汇总与派发（ADR-009 / ADR-019）。

- 每个工具是一条声明 `{"name", "label", "description", "input_schema", "handler"}`，说明和实现放在一处，
  `label` 是对话里那一行步骤显示的英文原文（译文进声明它的 App 的术语表），
  智能体配置（给模型看的工具表）与服务端派发都从这同一份生成，不会两边对不上。
- 共用工具在 `CORE_TOOLS`；各产品在自己 `hooks.py` 声明
  `pearnly_assistant_tools = ["<app>.<module>.TOOLS"]`（指向本产品的声明列表）。
- 工具一律在本站服务端、以当前登录用户身份执行：权限、租户、套账与数据范围沿用各产品现有服务的
  校验，工具只调已有服务，不绕过它们读库。业务拒绝用 `frappe.throw`，作为 `is_error` 结果交回模型。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import config


def whoami(_tool_input) -> str:
    user = frappe.session.user
    return json.dumps({
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        "product": config.product(),
        "company": frappe.conf.get("pearnly_company_name") or "",
        "language": frappe.local.lang or "",
    }, ensure_ascii=False)


def tax_id_lookup(tool_input) -> str:
    """税务局公开登记信息（同 ERP 录对方档、pearnly.com 税号工具）：缓存 7 天，查不到给原因，不抛错。"""
    from pearnly_core.revenue_department import service

    result = service.lookup(tool_input.get("tax_id"), tool_input.get("branch") or 0)
    return json.dumps(result, ensure_ascii=False, default=str)


CORE_TOOLS = [
    {"name": "pearnly_whoami",
     "label": "Checked your account",
     "description": "Return who the signed-in Pearnly user is: user id, full name, product, company and language. "
                    "Call it when you need to address the user or check which product and company you are working in.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_core.assistant.tools.whoami"},
    {"name": "pearnly_tax_id_lookup",
     "label": "Looked up a tax ID",
     "description": "Look up a Thai 13-digit tax ID in the Revenue Department's public register: registered name, "
                    "head office or branch, address, VAT registration date. Use it whenever the user asks who a tax ID "
                    "belongs to or wants a counterparty's official name or address. branch is the branch number "
                    "(0 = head office). error not_vat_registered means the ID exists but is not VAT-registered; "
                    "rd_unreachable means the Revenue Department did not answer, so say so and suggest trying later.",
     "input_schema": {"type": "object", "properties": {
         "tax_id": {"type": "string"}, "branch": {"type": "integer", "minimum": 0}}, "required": ["tax_id"]},
     "handler": "pearnly_core.assistant.tools.tax_id_lookup"},
]


def declared() -> list[dict]:
    found = {tool["name"]: tool for tool in CORE_TOOLS}
    for path in frappe.get_hooks("pearnly_assistant_tools") or []:
        for tool in frappe.get_attr(path) or []:
            found[tool["name"]] = tool
    return [found[name] for name in sorted(found)]


def labels() -> dict[str, str]:
    """对话里步骤行显示的名字（按当前用户语言）；没写 label 的工具显示原名。"""
    return {t["name"]: _(t["label"]) if t.get("label") else t["name"] for t in declared()}


def run(name: str, tool_input) -> tuple[str, bool]:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    if not tool:
        return _("This tool is not available on this site."), True
    try:
        return str(frappe.get_attr(tool["handler"])(tool_input if isinstance(tool_input, dict) else {})), False
    except (frappe.ValidationError, frappe.PermissionError) as err:
        frappe.db.rollback()
        return str(err) or _("The request was refused."), True
