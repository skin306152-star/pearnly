"""网页「自动录入规则」页的接口（浏览器会话；小助手的机器接口在 `machine.py`）。"""
import frappe

from pearnly_cowork.folder_intake import rules


@frappe.whitelist()
def listing():
    return rules.listing()


@frappe.whitelist()
def options(connection, account_set_key=None):
    return rules.options(connection, account_set_key)


@frappe.whitelist(methods=["POST"])
def save(values):
    return rules.save(values)


@frappe.whitelist(methods=["POST"])
def set_enabled(name, enabled):
    return rules.set_enabled(name, enabled)


@frappe.whitelist(methods=["POST"])
def run_now(name):
    return rules.run_now(name)


@frappe.whitelist()
def runs(name):
    return rules.runs(name)
