"""集成域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.integrations import binding, intake


@frappe.whitelist()
def api_line_bot(**kwargs):
    """本站点 LINE 官方号对外身份（名字、Basic ID、加好友链接）。"""
    return binding.account()


@frappe.whitelist()
def api_line_binding(**kwargs):
    return binding.status()


@frappe.whitelist(methods=["POST"])
def api_line_binding_code(**kwargs):
    """发一个新的绑定码（旧的未用码同时作废）。"""
    return binding.start()


@frappe.whitelist(methods=["POST"])
def api_line_unbind(**kwargs):
    return binding.unbind()


# ── LINE 收单：卡片「编辑」打开网页端原生页 → 提交入账 ─────────────────────────


@frappe.whitelist(methods=["POST"])
def api_open_line_intake(name):
    """POST：打开时可能要把当前公司切到这张单在 LINE 里选的那家（写用户默认值）。"""
    return intake.open_for_edit(name)


@frappe.whitelist(methods=["POST"])
def api_submit_line_intake(name, payload):
    return intake.submit(name, frappe.parse_json(payload) if isinstance(payload, str) else payload)
