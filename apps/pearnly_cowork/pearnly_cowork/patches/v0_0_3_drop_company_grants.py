"""Cowork 不做公司数据隔离（用户 2026-09-26）：撤掉按公司 / 客户授权的官方 User Permission。

这些行原来由团队管理「添加用户」写入（每人一份本站点公司资料），`access.scope` 再按它分人。
现在服务端不再按人分公司；留着它们，框架自己的 User Permission 过滤仍会按公司挡住一部分老账号，
与新账号不一致。只动 `allow` 是 Cowork 公司 / 客户的行，别的 User Permission 不碰。
"""

DOCTYPES = ("Cowork Workspace", "Cowork Client")


def execute():
    import frappe

    if not frappe.db.table_exists("User Permission"):
        return
    frappe.db.delete("User Permission", {"allow": ("in", DOCTYPES)})
    frappe.cache.delete_value("user_permissions")
