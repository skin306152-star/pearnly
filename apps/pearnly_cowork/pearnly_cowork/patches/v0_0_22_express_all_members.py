"""CC 桌面版多台电脑第 4 批（ADR-021，方案 2.9 / 4.1 第 5 步，D2）：现有 Express 主连接全所可用。

每个归属下的 Express 主连接（`connections.service.main_connections`：不挂客户、启用、代数为 0、最早建的那条）
``all_members = 1``：成员在自己的电脑上登录 CC 就能加入事务所的 Express 池，不用逐个加使用名单。
直接写字段，**不升 ``record_revision``**：放宽名单不该作废任何排队的单。MR.ERP、后建的 Express 连接、挂客户的
连接都不动（名单照旧）。幂等。
"""
import frappe

CONNECTION = "Cowork ERP Connection"


def execute():
    if not frappe.db.has_column(CONNECTION, "all_members"):
        return
    from pearnly_cowork.connections import service

    for (workspace,) in frappe.db.sql(f"""SELECT DISTINCT workspace FROM `tab{CONNECTION}` WHERE adapter='express'"""):
        main = service.main_connections(workspace).get("express")
        if main and not int(frappe.db.get_value(CONNECTION, main, "all_members") or 0):
            frappe.db.set_value(CONNECTION, main, "all_members", 1, update_modified=False)
