"""补齐采购单的同一张票身份列（`ticket_key` / `ticket_level`，复核 5 批之第 1 批）。

新保存的由 `PurchaseDocument.validate` 维护（`express.tickets.purchase_identity`）；这里只给加列之前的旧单按同一判据回填。
直接写列、不改修改时间，不经保存校验（已提交的单不许再保存）。按名字分批读，重跑幂等。
"""
BATCH = 500


def execute():
    import frappe
    from pearnly_erp_portal.express import push, tickets

    fields = ["name", "company", "doc_no", "doc_date", "grand_total", "supplier_tax_id", "supplier_branch_type",
              "supplier_branch_no", "supplier_name"]
    last = ""
    while True:
        rows = frappe.get_all(push.PURCHASE, filters={"name": [">", last]}, fields=fields, order_by="name asc",
                              limit_page_length=BATCH)
        for row in rows:
            key, level = tickets.purchase_identity(row)
            frappe.db.set_value(push.PURCHASE, row.name, {"ticket_key": key, "ticket_level": level}, update_modified=False)
        if len(rows) < BATCH:
            break
        last = rows[-1].name
        frappe.db.commit()
