"""开户自动建的默认公司以前不建仓（生产 gize 开户后 0 个仓，10-29）：给「0 个仓、没接 Express、也没连上 Express」的公司补一个默认仓
（同「新建公司」表单的同一段代码与默认仓名，`masters.company.ensure_warehouse`）。接了 / 连上 Express 的公司仓库以 Express 同步为准，不动。幂等。"""
import frappe


def execute():
    from pearnly_erp_portal.masters import company as companies

    for name in frappe.get_all("ERP Company", pluck="name", limit_page_length=0):  # bounded: 一个租户的公司
        made = companies.ensure_warehouse(name)
        if made:
            print(f"default warehouse {made} for company {name}")
    frappe.db.commit()
