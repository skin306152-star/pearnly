"""「使用教程」「识别记录」「推送日志」「对账中心」四个占位页删掉（主会话 2026-11-08：没有承接的不开放）：从没实现过（只显示「未建」）、
不在任何产品的侧栏，源码目录已删；站点上已同步的 Page 记录（连同它的角色行）一并删掉。没有业务数据。重跑无副作用。"""
import frappe

PAGES = ("pearnly-guide", "pearnly-history", "pearnly-push-logs", "pearnly-reconcile")


def execute():
    for name in PAGES:
        if frappe.db.exists("Page", name):
            frappe.delete_doc("Page", name, force=True, ignore_permissions=True)
