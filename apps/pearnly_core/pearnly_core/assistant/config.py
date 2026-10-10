"""智能体的站点配置（ADR-019）。

- `pearnly_assistant_api_key`：本租户的 Anthropic 密钥。**只由租户管理后台写入**：开户时填，之后在后台
  修改 / 更换，经开通执行器推到本站 `site_config.json`；用户从不接触。站点只读不写。
- 智能体与运行环境不配置：站点按这把密钥自动在其所属账号里建好或更新（见 `provision`），
  所以换密钥 / 换账号不需要任何手工步骤。

只有业务站点（开通时写过 `pearnly_product`）能用；本机管理后台没有产品，直接拒绝。
"""
import frappe
from frappe import _


def product() -> str:
    code = str(frappe.conf.get("pearnly_product") or "").strip()
    if not code:
        frappe.throw(_("The assistant is only available on product sites."), frappe.PermissionError)
    return code


def api_key() -> str:
    key = str(frappe.conf.get("pearnly_assistant_api_key") or "").strip()
    if not key:
        frappe.throw(_("The assistant is not set up for this site yet."))
    return key
