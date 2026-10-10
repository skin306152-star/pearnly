"""智能体工具：声明、汇总与派发（ADR-009 / ADR-019）。

- 每个工具是一条声明 `{"name", "description", "input_schema", "handler"}`，说明和实现放在一处，
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


CORE_TOOLS = [
    {"name": "pearnly_whoami",
     "description": "Return who the signed-in Pearnly user is: user id, full name, product, company and language. "
                    "Call it when you need to address the user or check which product and company you are working in.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_core.assistant.tools.whoami"},
]


def declared() -> list[dict]:
    found = {tool["name"]: tool for tool in CORE_TOOLS}
    for path in frappe.get_hooks("pearnly_assistant_tools") or []:
        for tool in frappe.get_attr(path) or []:
            found[tool["name"]] = tool
    return [found[name] for name in sorted(found)]


def run(name: str, tool_input) -> tuple[str, bool]:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    if not tool:
        return _("This tool is not available on this site."), True
    try:
        return str(frappe.get_attr(tool["handler"])(tool_input if isinstance(tool_input, dict) else {})), False
    except (frappe.ValidationError, frappe.PermissionError) as err:
        frappe.db.rollback()
        return str(err) or _("The request was refused."), True
