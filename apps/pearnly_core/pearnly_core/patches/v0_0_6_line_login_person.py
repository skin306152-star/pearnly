"""LINE 登录改为"每个 LINE 一个专用身份、站点按有效绑定认人"（2026-10-04）。

已生效的绑定标成待同步：每小时对账会让控制面建专用身份、把原来挂在租户账号上的关联迁过去。
"""
import frappe


def execute():
    frappe.db.sql("""update `tabPearnly LINE Binding` set login_link = 'pending'
                     where status = 'active' and ifnull(login_link, '') != 'pending'""")
