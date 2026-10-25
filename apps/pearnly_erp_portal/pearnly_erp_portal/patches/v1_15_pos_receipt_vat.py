"""收银小票回填税额（主会话 10-25）：标价含税，增值税登记户的小票逐行拆出税额（`cashier.vat`），应收合计不变。

公司上还没记增值税登记状态的，先按税务局查过的结果补记（只看缓存，迁移时不连外部接口）；记得上的公司把还没写进 Express 的完成小票
按状态重算。还不知道的公司不动，第一次写收银日汇总时在单据上问一次，答了再重算。"""
import frappe


def execute():
    if not frappe.db.has_column("ERP Company", "vat_registered") or not frappe.db.has_column("ERP Sale Line", "vat_amount"):
        return
    from pearnly_erp_portal.cashier import vat
    from pearnly_erp_portal.masters import company as companies

    for row in frappe.get_all("ERP Company", fields=["name", "vat_registered"], limit_page_length=0):  # bounded: 这个租户的公司
        if row.vat_registered in companies.VAT_STATES:
            vat.refresh_company(row.name)
        else:
            companies.vat_registered(row.name)  # 按缓存补记得上的，补记时就重算了（`company._vat_changed`）
    frappe.db.commit()
