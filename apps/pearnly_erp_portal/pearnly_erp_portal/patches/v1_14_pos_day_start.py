"""收银日汇总的起始日（出入库第一期 B3a，验收 10-25）：已经接上 Express 的公司记成这次上线当天，之前的收银小票不补写成 Express 现销
（会计可能已手录过、期间可能已关账）。之后才接上的公司在第一次主档同步时记那天（`express.masters.apply`）。"""
import frappe
from frappe.utils import nowdate


def execute():
    if not frappe.db.has_column("ERP Company", "express_pos_from"):
        return
    # 站点时区的今天（数据库 CURDATE() 是 UTC，曼谷 00:00–07:00 部署会记成前一天；验收 10-25）
    frappe.db.sql("""UPDATE `tabERP Company` SET express_pos_from = %s
        WHERE COALESCE(express_book, '') != '' AND express_pos_from IS NULL""", (nowdate(),))
