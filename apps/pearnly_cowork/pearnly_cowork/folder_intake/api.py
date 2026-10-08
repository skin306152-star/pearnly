"""网页「自动录入规则」页的接口（浏览器会话；小助手的机器接口在 `machine.py`）。"""
import frappe

from pearnly_cowork.folder_intake import rules


@frappe.whitelist()
def listing():
    return rules.listing()


@frappe.whitelist()
def options(connection, account_set_key=None):
    # 读目录 / 存货科目组都锁这条连接行，与小助手心跳、刷新目录并发时可能死锁：只读请求，回滚重试即可。
    for attempt in range(3):
        try:
            return rules.options(connection, account_set_key)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def save(values):
    return rules.save(values)


@frappe.whitelist(methods=["POST"])
def set_enabled(name, enabled):
    return rules.set_enabled(name, enabled)


@frappe.whitelist(methods=["POST"])
def delete(names):
    return rules.delete(names)


@frappe.whitelist(methods=["POST"])
def run_now(name):
    return rules.run_now(name)


@frappe.whitelist()
def runs(name):
    return rules.runs(name)
