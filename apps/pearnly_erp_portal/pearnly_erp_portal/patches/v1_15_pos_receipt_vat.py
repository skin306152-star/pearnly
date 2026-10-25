"""收银小票回填税额（主会话 10-26 定范围）：标价含税，**已确认是增值税登记户**的公司把还没写进 Express 的完成小票逐行拆出税额
（`cashier.vat`），应收合计不变。状态未知的公司不动，等第一次写收银日汇总时问完再补；非登记户一律不拆（小票本来就是 0）。

「已确认」= 公司上记着「是」，或税务局查询缓存里查得到 VAT 登记（只看缓存，迁移时不连外部接口；查到就记在公司上）。
可以重跑，结果不变（第二次没有小票会变）。跑完打印影响的公司数和小票数，部署记录照抄。"""
import frappe


def execute():
    if not frappe.db.has_column("ERP Company", "vat_registered") or not frappe.db.has_column("ERP Sale Line", "vat_amount"):
        return
    from pearnly_erp_portal.cashier import vat
    from pearnly_erp_portal.masters import company as companies

    touched, receipts = 0, 0
    for row in frappe.get_all("ERP Company", fields=["name", "vat_registered", "tax_id", "branch"],  # bounded: 这个租户的公司
                              limit_page_length=0):
        state = row.vat_registered
        if state not in companies.VAT_STATES:
            state = companies._rd_vat(row.tax_id, row.branch)  # 只看缓存
            if state:
                frappe.db.set_value("ERP Company", row.name, "vat_registered", state, update_modified=False)
        if state != companies.VAT_YES:
            continue
        changed = vat.refresh_company(row.name)
        if changed:
            touched, receipts = touched + 1, receipts + changed
    frappe.db.commit()
    print(f"v1_15_pos_receipt_vat: {touched} companies, {receipts} receipts")
