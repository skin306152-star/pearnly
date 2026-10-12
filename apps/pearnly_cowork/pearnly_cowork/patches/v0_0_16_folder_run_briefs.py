"""定时任务的完成简报（2026-10-12）按运行记录的 `brief_state` 发：上线前已有的运行一律记成 `skipped`，
不给历史运行补发简报。重跑只会得到同样的值（只改还空着或待发的）。"""
import frappe

RUN = "Cowork Folder Intake Run"


def execute():
    if not frappe.db.has_column(RUN, "brief_state"):
        return
    frappe.db.sql(f"UPDATE `tab{RUN}` SET brief_state = 'skipped' WHERE IFNULL(brief_state, '') IN ('', 'pending')")
