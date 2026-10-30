"""收银台挂主仓（10-30）：接 Express 的公司同步出多个仓时，默认收银台原来不带仓、收银报「收银台没有仓」；Express 删了仓、
同步停用后，挂在那个仓上的收银台卖货报「仓已停用」。给每家公司没挂仓、或挂着停用 / 别家仓的收银台改挂主仓
（`cashier.terminals.attach_main`，主仓 = 最先建的启用仓）。幂等，打印改了哪几台。"""
import frappe


def execute():
    from pearnly_erp_portal.cashier import terminals

    for company in frappe.get_all("ERP Company", pluck="name", limit_page_length=0):  # bounded: 一个租户的公司
        for name in terminals.attach_main(company):
            print(f"terminal {name} of {company} now sells from the main warehouse")
    frappe.db.commit()
