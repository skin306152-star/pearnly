"""Multi-company: put the rows that existed before the company column under the default company.

一租户多公司是后加的能力：`ERP Warehouse` / `Purchase Document` / `ERP Sales Document`
新增了必填的 `company`。老数据必须归到租户的默认公司上，否则（a）这些单据一保存就报
"Company 是必填"，(b) 入库/出库找不到仓库。幂等：只补空值。
"""
import frappe

from pearnly_erp_portal.masters import company

TARGETS = ("ERP Warehouse", "Purchase Document", "ERP Sales Document")


def execute():
    default = company.ensure_default()
    for doctype in TARGETS:
        # 固定标识符来自我们自己的 schema，没有用户输入拼进 SQL。
        frappe.db.sql(f"update `tab{doctype}` set company=%s where company is null or company=''",
                      (default,))
    # 老的那个默认仓库要挂到默认公司上：不然它的出入库会算到别家去。
    default_warehouse = (frappe.db.get_value("ERP Warehouse", {"is_default": 1}, "name")
                         or frappe.db.get_value("ERP Warehouse", {}, "name", order_by="creation asc"))
    if default_warehouse:
        frappe.db.set_value("ERP Warehouse", default_warehouse,
                            {"company": default, "is_default": 1}, update_modified=False)
    frappe.db.commit()
    frappe.clear_cache()
