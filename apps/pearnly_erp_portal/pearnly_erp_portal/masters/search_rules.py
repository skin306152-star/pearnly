"""主档按打的字找的规则（纯逻辑，不依赖框架）：选择框（`link_search`）、LINE 选客户 / 供应商（`line_flow`）、收款选客户
（`collection.service.customers`）同一份（11-15 真机：各写各的，有的认税号、有的不认）。

``COLUMNS`` 与各 DocType 的 ``search_fields`` 一致（名称列在前），由 `tests/test_master_search_rules.py` 对照 DocType 定义守住。
"""

TITLES = {"ERP Product": "product_name", "ERP Customer": "customer_name", "ERP Supplier": "supplier_name"}
COLUMNS = {
    "ERP Product": ("product_name", "code", "express_code", "internal_code", "barcode", "qr_payload", "attribute"),
    "ERP Customer": ("customer_name", "express_code", "tax_id", "contact_phone"),
    "ERP Supplier": ("supplier_name", "express_code", "tax_id", "phone"),
}


def columns(doctype) -> tuple:
    return COLUMNS[doctype]


def pattern(text) -> str:
    """``like`` 用的串：几个词按顺序都要有（「%甲%乙%」）；不分大小写由库的排序规则负责。"""
    return "%" + "%".join(str(text or "").split()) + "%"


def or_filters(doctype, text) -> list:
    """``frappe.get_all(or_filters=…)`` 版：单据号 ``name`` 与 :data:`COLUMNS` 任一列包含。没打字回空。"""
    return [[column, "like", pattern(text)] for column in ("name", *COLUMNS[doctype])] if str(text or "").split() else []
