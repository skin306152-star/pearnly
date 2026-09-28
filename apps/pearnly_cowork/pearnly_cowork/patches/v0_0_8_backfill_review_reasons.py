"""给加 `review_reasons` 列之前的识别记录按同一规则（`history/review.py`）算一次需复核原因。

新写入由 `storage.validate_document` 维护；这里只回填旧行。直接写列、不改修改时间与记录版本，
不经复核服务（不是一次编辑）。已推送成功的写空清单。重跑幂等。
"""


def execute():
    import frappe
    from pearnly_cowork.history import storage

    rows = frappe.get_all(storage.DOCTYPE, fields=["name", "workspace", "pages", "last_push_status", "edit_count",
                                                     "duplicate_check_status"], limit_page_length=0)
    for row in rows:
        try:
            pages = storage.parse(row.pages or "[]") or []
            found = storage.review_reasons(frappe._dict(row), pages)
        except (TypeError, ValueError, AttributeError, frappe.ValidationError):  # 坏页面不回填，照旧
            continue
        frappe.db.set_value(storage.DOCTYPE, row.name, "review_reasons", storage.encode(found), update_modified=False)
