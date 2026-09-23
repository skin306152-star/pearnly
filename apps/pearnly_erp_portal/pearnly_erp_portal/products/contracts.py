"""商品主数据的纯规则：不依赖 Frappe、IO 或上层流程。

字段集与旧线上一致（`routes/products_routes.py` 的 ProductCreate），长度上限也照旧，
这样迁移进来的数据不会被新平台莫名拒绝。
"""

import re
from decimal import Decimal, InvalidOperation

# 与旧线上相同的长度上限。
MAX_NAME = 300
MAX_CODE = 100
MAX_BARCODE = 100
MAX_QR = 500
MAX_UNIT = 50
MAX_BASE_UNIT = 50
MAX_IMAGE_URL = 1000

# 有成本字段，取数时按岗位遮蔽。`cost_set` 是"参考成本是否真的设过"的标记，
# 它跟着成本一起遮蔽 —— 否则看不到成本的人会读到一个"未设"的假答案。
COST_FIELDS = ("default_cost", "cost_set")

# 条码/编码唯一性：同一租户内不能重复。旧线上是三条唯一索引，撞哪个报哪个。
UNIQUE_FIELDS = ("code", "barcode")

# 界面与表格上的"码"是一格（用户 2026-09-22 口径）：条码与二维码内容合并显示，
# 落库仍旧线上那两列。这一格该写哪一列由**内容形状**决定 —— 条码是短 ASCII
# （Code39/128 的字符集，外加 EAN/UPC 的数字），其余（中文、含空格的长串、超过条码
# 上限的）归二维码内容。两列长度上限不同（100 / 500），塞错了会被 clean_text 判成
# Invalid value，所以形状判断必须与长度上限一起做。
# 前端的同一份规则在 `public/desk/products.js` 的 BARCODE_SHAPE，两处要一起改。
BARCODE_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-$/+.% ]{0," + str(MAX_BARCODE - 1) + r"}$")


def code_field(value) -> str:
    """合并的那一格该落哪一列：条码还是二维码内容。空值按条码（唯一性在条码上）。"""
    text = str(value or "").strip()
    if not text:
        return "barcode"
    return "barcode" if BARCODE_SHAPE.match(text) else "qr_payload"


def shown_code(row) -> str:
    """这一格**显示**什么：条码优先，没有条码就用二维码内容（两列都可能为空）。"""
    return str((row or {}).get("barcode") or (row or {}).get("qr_payload") or "")


# ERP 门户的商品编码由系统发，不从表单收：旧线上（`services/erp/product_numbers.py`
# 的 `P-` + 六位）就是这么发的，手输的编码会在单据行、扫码建品与导入之间对不上。
CODE_PREFIX = "P-"
CODE_DIGITS = 6
_CODE_RE = re.compile(rf"^{CODE_PREFIX}(\d+)$")


def next_code(existing) -> str:
    """在已有编码里取最大序号 +1。认不出的编码（旧的别家规则、人肉填的）跳过不数。"""
    used = [int(match.group(1))
            for match in (_CODE_RE.match(str(code or "")) for code in existing) if match]
    return f"{CODE_PREFIX}{max(used, default=0) + 1:0{CODE_DIGITS}d}"


def decimal_or_none(value):
    """金额/数量解析：空值保持空（不是 0）。

    旧线上特意强调过这一点：`unit_price` 用 0 当默认值会让"没设价"和"真的 ฿0"
    在库里变成同一个值，收银台的零元闸就拦不住。新平台沿用"空就是空"。
    """
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid number") from None
    if not result.is_finite() or result < 0:
        raise ValueError("invalid number")
    return result


def unit_factor(value) -> Decimal:
    """单位换算系数：必须大于 0（旧线上 `factor_to_base > 0`）。"""
    factor = decimal_or_none(value)
    if factor is None or factor <= 0:
        raise ValueError("invalid factor")
    return factor


def require_name(name) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    if len(name) > MAX_NAME:
        raise ValueError("name too long")
    return name


def clean_text(value, limit: int):
    if value in (None, ""):
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) > limit:
        raise ValueError("too long")
    return value


def mask_costs(row: dict, visible: bool) -> dict:
    """按岗位遮蔽成本字段：不可见时返回空值，而不是把值交给前端再隐藏。"""
    if visible:
        return row
    for field in COST_FIELDS:
        if field in row:
            row[field] = None
    return row
