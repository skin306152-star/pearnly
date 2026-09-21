"""商品主数据的纯规则：不依赖 Frappe、IO 或上层流程。

字段集与旧线上一致（`routes/products_routes.py` 的 ProductCreate），长度上限也照旧，
这样迁移进来的数据不会被新平台莫名拒绝。
"""

import re
from decimal import Decimal, InvalidOperation

# 与旧线上相同的长度上限。
MAX_NAME = 300
# 英文/中文名与泰文名同长（旧线上三个名字都是 300）。
MAX_NAME_EN = 300
MAX_NAME_ZH = 300
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
