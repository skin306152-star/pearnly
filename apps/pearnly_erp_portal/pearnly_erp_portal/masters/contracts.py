"""买方与公司资料的纯规则：不依赖 Frappe、IO 或上层流程。

买方归一化照旧线上 `services/sales/buyer.py` 的"保险层"口径：类型决定哪些字段有意义，
不该有的值一律清空而不是留着。
"""

PARTY_TYPES = ("company", "person", "foreign", "anonymous")
DEFAULT_PARTY = "company"

BRANCH_TYPES = ("hq", "branch")

MAX_NAME = 200
MAX_SHORT = 80
MAX_TAX_ID = 20
MAX_ADDRESS = 500
MAX_CONTACT = 100
MAX_PHONE = 50
MAX_EMAIL = 200
MAX_NOTE = 1000
MAX_COLOR = 20
MAX_BRANCH_NO = 20
MAX_PROMPTPAY = 50


def clean(value, limit: int):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError("too long")
    return text


def normalize_buyer(raw: dict) -> dict:
    """把请求里的买方块归一化成落库形状。

    - 匿名买家没有税号；
    - 非公司买家没有分店概念；
    - 公司但非分店态清掉分店号（避免"总店带分店号"）。
    """
    raw = raw or {}
    party = raw.get("party_type") or DEFAULT_PARTY
    if party not in PARTY_TYPES:
        party = DEFAULT_PARTY

    tax_id = clean(raw.get("tax_id"), MAX_TAX_ID)
    if party in ("anonymous", "person", "foreign"):
        tax_id = None

    if party == "company":
        branch_type = raw.get("branch_type") or "hq"
        if branch_type not in BRANCH_TYPES:
            branch_type = "hq"
        branch_no = clean(raw.get("branch_no"), MAX_BRANCH_NO) if branch_type == "branch" else None
    else:
        branch_type = None
        branch_no = None

    return {
        "customer_name": clean(raw.get("customer_name") or raw.get("name"), MAX_NAME),
        "short_name": clean(raw.get("short_name"), MAX_SHORT),
        "party_type": party,
        "tax_id": tax_id,
        "branch_type": branch_type,
        "branch_no": branch_no,
        "address": clean(raw.get("address"), MAX_ADDRESS),
        "contact_person": clean(raw.get("contact_person"), MAX_CONTACT),
        "contact_phone": clean(raw.get("contact_phone") or raw.get("phone"), MAX_PHONE),
        "contact_email": clean(raw.get("contact_email") or raw.get("email"), MAX_EMAIL),
        "promptpay_id": clean(raw.get("promptpay_id"), MAX_PROMPTPAY),
        "color": clean(raw.get("color"), MAX_COLOR),
    }


def missing_for_tax_invoice(customer: dict) -> list[str]:
    """开税票时买方还缺什么。由开票侧调用，这里只回答"缺哪些字段"。"""
    missing = []
    if customer.get("party_type") == "anonymous":
        return ["party_type"]
    if not customer.get("customer_name"):
        missing.append("customer_name")
    if not customer.get("address"):
        missing.append("address")
    if customer.get("party_type") == "company":
        if not customer.get("tax_id"):
            missing.append("tax_id")
        if customer.get("branch_type") == "branch" and not customer.get("branch_no"):
            missing.append("branch_no")
    return missing


def fiscal_month_ok(value) -> bool:
    try:
        return 1 <= int(value) <= 12
    except (TypeError, ValueError):
        return False
