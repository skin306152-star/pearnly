"""公司设定（用户 10-29）：已连 Express 的公司设为「接 Express」，类型一律默认普通。

「已连」= 同步过 Express 账套（公司上记着 `express_book`），或 CC 桌面版报过它（税号 + 分店）的账套。幂等：只补没设过的。
"""
import frappe


def execute():
    from pearnly_erp_portal.express import push

    for row in frappe.get_all("ERP Company", fields=["name", "company_type", "express_enabled", "express_book"],
                              limit_page_length=0):  # bounded: 一个租户的公司
        values = {}
        if not row.company_type:
            values["company_type"] = "general"
        if not row.express_enabled and (row.express_book or push.connected(row.name)):
            values["express_enabled"] = 1
        if values:
            frappe.db.set_value("ERP Company", row.name, values, update_modified=False)
    frappe.db.commit()
