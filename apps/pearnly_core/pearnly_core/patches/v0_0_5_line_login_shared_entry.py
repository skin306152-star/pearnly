"""LINE 登录入口由"每站点一个"改为"每个 LINE Login 频道一个"（用户 2026-09-26：密钥全平台只配一次）。

已关联的绑定是挂在旧入口上的：清空关联状态，由每小时对账在新入口下重新关联（幂等、会重试）。
"""
import frappe


def execute():
    frappe.db.sql("update `tabPearnly LINE Binding` set login_link = '' where ifnull(login_link, '') != ''")
