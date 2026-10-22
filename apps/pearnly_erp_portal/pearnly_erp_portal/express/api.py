"""管理员手动同步一家公司的 Express 主档与库存（公司页按钮用）。"""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import masters

WAIT = 30  # 电脑读一本账套一般十几秒：按钮在请求里等它读完，超了就回「读取中」，后台接着等


@frappe.whitelist(methods=["POST"])
def sync_company(company):
    guard.require("team.manage")  # 只给管理员（ADMIN_CAPABILITIES）
    if not isinstance(company, str) or not frappe.db.exists(masters.COMPANY, company):
        frappe.throw(frappe._("Company not found"), frappe.DoesNotExistError)
    result = masters.sync(company, wait=WAIT)
    if result.get("state") == "reading":
        masters.enqueue(company)
    return result
