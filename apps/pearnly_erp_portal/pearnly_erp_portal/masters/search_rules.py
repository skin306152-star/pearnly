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


# Express 主档的称谓 / 法人前缀（PRENAM 常见值，长的在前）。档名是「前缀 + 空格 + 核心名」（`express.contracts.display_name`），
# 人常连着打（「ร้านลีฟ」）：打的字以前缀开头就在前缀后断开，连着打、隔空格打、只打核心名都找得到。
PREFIXES = ("ห้างหุ้นส่วนจำกัด", "ห้างหุ้นส่วนสามัญ", "ห้างหุ้นส่วน", "บริษัทจำกัด", "บริษัท", "หจก.", "หจก", "หสน.", "บจก.", "บจก",
            "บมจ.", "บจ.", "ร้าน", "นางสาว", "นาย", "นาง", "คุณ")


def words(text) -> list:
    found = str(text or "").split()
    if found:
        head = next((p for p in PREFIXES if found[0].startswith(p) and len(found[0]) > len(p)), "")
        if head:
            found[:1] = [head, found[0][len(head):]]
    return found


def pattern(text) -> str:
    """``like`` 用的串：几个词按顺序都要有（「%甲%乙%」）；不分大小写由库的排序规则负责。"""
    return "%" + "%".join(words(text)) + "%"


# 电话列：库里有的带横线 / 空格（081-234-5678），有的不带；打的字只要是电话样子，两边都去掉这些再比（11-15 真机：带横线才搜得到）
PHONES = {"ERP Customer": ("contact_phone",), "ERP Supplier": ("phone",)}
PHONE_MARKS = ("-", " ", ".", "(", ")")


def phone_digits(text) -> str:
    """打的字像电话（只有数字与 - 空格 . ( ) +，至少 3 位数字）时回去掉符号的数字串，否则空串。"""
    text = str(text or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 3 or any(not (ch.isdigit() or ch in PHONE_MARKS or ch == "+") for ch in text):
        return ""
    return digits


def phone_column(column) -> str:
    """SQL：这一列去掉电话符号后的值（与 :func:`phone_digits` 同一组符号）。"""
    expr = f"ifnull(`{column}`, '')"
    for mark in PHONE_MARKS:
        expr = f"replace({expr}, '{mark}', '')"
    return expr
