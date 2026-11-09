"""补齐识别记录的同一张票身份列（`seller_branch` / `buyer_branch` / `ticket_number` / `ticket_key` / `ticket_level`，复核 5 批之第 1 批）。

新写入由 `storage.validate_document` 随摘要一起维护（`extract_ticket_fields`）；这里只给加列之前的旧行按同一判据回填。
直接写列、不改修改时间与记录版本，不经复核服务（不是一次编辑）。按名字分批读，重跑幂等。
"""
BATCH = 500


def execute():
    import frappe
    from pearnly_core.express_rules.contracts.history import extract_ticket_fields
    from pearnly_cowork.history import storage

    last = ""
    while True:
        rows = frappe.get_all(storage.DOCTYPE, filters={"name": [">", last]}, fields=["name", "pages"], order_by="name asc",
                              limit_page_length=BATCH)
        for row in rows:
            try:
                values = extract_ticket_fields(storage.parse(row.pages or "[]") or [])
            except (TypeError, ValueError, AttributeError):
                continue
            frappe.db.set_value(storage.DOCTYPE, row.name, values, update_modified=False)
        if len(rows) < BATCH:
            break
        last = rows[-1].name
        frappe.db.commit()
