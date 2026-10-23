"""补齐识别记录的买卖方列（`seller_tax` / `buyer_name` / `buyer_tax`）。

这三列是列表排序用的摘要，与 `seller_name` 同一主页面（`extract_party_fields`），新写入由
`storage.validate_document` 维护；这里只给加列之前的旧行按同一判据回填。直接写列、不改修改时间与
记录版本，不经复核服务（不是一次编辑）。重跑幂等。
"""


def execute():
    import frappe
    from pearnly_core.express_rules.contracts.history import extract_party_fields
    from pearnly_cowork.history import storage

    for row in frappe.get_all(storage.DOCTYPE, fields=["name", "pages"], limit_page_length=0):
        try:
            values = extract_party_fields(storage.parse(row.pages or "[]") or [])
        except (TypeError, ValueError, AttributeError):
            continue
        frappe.db.set_value(storage.DOCTYPE, row.name, values, update_modified=False)
