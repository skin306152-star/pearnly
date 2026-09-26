"""LINE 绑定的接口层（网页侧，只给当前登录账号）。"""

import frappe

from pearnly_cowork.line import binding


@frappe.whitelist()
def api_line_binding():
    return binding.status()


@frappe.whitelist(methods=["POST"])
def api_line_binding_code():
    """发一个新的绑定码（旧的未用码同时作废）。"""
    return binding.start()


@frappe.whitelist(methods=["POST"])
def api_line_unbind():
    return binding.unbind()
