"""定时任务的「读文件夹的小助手」（`reader`，2026-10-09）：以前读文件夹的就是推送目标那条 Express 连接自己的小助手，
旧任务补成它自己的连接。之后 MR.ERP 任务可以用同一工作空间里任一台小助手读文件夹（同时用两种 ERP 的不用再配第二个配对码）。
重跑只把还空着的补上。"""
import frappe

RULE = "Cowork Folder Intake Rule"


def execute():
    if not frappe.db.has_column(RULE, "reader"):
        return
    frappe.db.sql(f"UPDATE `tab{RULE}` SET reader = connection WHERE IFNULL(reader, '') = ''")
