"""买方与公司资料的纯规则：不依赖 Frappe、IO 或上层流程。

买方归一化照旧线上 `services/sales/buyer.py` 的"保险层"口径：类型决定哪些字段有意义，
不该有的值一律清空而不是留着。
"""

import re

from pearnly_core.tax_documents.amounts import digits as _digits, valid_tax_id

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


# 泰国税号：公司、供应商、客户与采购识别同一套校验（录制问题 #11）。校验位算法在共用层
# （`pearnly_core.tax_documents`，识别也用它）。13 位数字一律验校验位；本公司的税号必须是 13 位。
# 供应商 / 客户可能是外国主体，不是 13 位数字的照旧放行。
TAX_ID_FORMAT = "format"
TAX_ID_CHECKSUM = "checksum"


def tax_id_problem(value, *, thai_required=False):
    """税号哪里不对：`format`（必须 13 位而不是）、`checksum`（13 位但校验位不对）；没问题返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    raw = _digits(text)
    # 只由数字加常见分隔（短横、空格）组成、共 13 位数字，才按泰国税号验。
    if len(raw) != 13 or not re.fullmatch(r"[\d\s-]+", text):
        return TAX_ID_FORMAT if thai_required else None
    return None if valid_tax_id(raw) else TAX_ID_CHECKSUM


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


def fiscal_month_ok(value) -> bool:
    try:
        return 1 <= int(value) <= 12
    except (TypeError, ValueError):
        return False
