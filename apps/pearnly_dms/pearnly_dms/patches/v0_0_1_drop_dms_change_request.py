"""删除 DMS 审批的申请表（用户 2026-10-05：审批不做，整条链已删；审批策略本来就关着，从没提审过）。

上线前核对生产 ris / testdms 这张表都没有数据。可重复执行：表定义与表都没有时什么也不做。
"""
import frappe

DOCTYPE = "DMS Change Request"


def execute():
    if frappe.db.exists("DocType", DOCTYPE):
        frappe.delete_doc("DocType", DOCTYPE, ignore_missing=True, force=True)
    if frappe.db.table_exists(DOCTYPE):
        frappe.db.sql_ddl(f"DROP TABLE `tab{DOCTYPE}`")
