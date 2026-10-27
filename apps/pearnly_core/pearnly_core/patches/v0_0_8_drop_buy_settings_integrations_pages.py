"""ERP 的「采购设置」「集成」两页不做（用户 2026-10-27：「『采购设置』『集成』是不要的」）：源码目录已删，
站点上已同步的 Page 记录（连同它的角色行）一并删掉。两页从没实现过（只显示「未建」），没有业务数据。重跑无副作用。"""
import frappe

PAGES = ("pearnly-buy-settings", "pearnly-integrations")


def execute():
    for name in PAGES:
        if frappe.db.exists("Page", name):
            frappe.delete_doc("Page", name, force=True, ignore_permissions=True)
