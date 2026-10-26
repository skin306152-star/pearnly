"""单据打印的纯规则（不依赖框架、不做 IO）：版面照岳父样板 1:1，每页 10 行，ERP 没有的栏位留空线手写，不冒充。

第二批（采购订单、报价单、销售单、收银小票、交班小结、盘点表，主会话 10-26 审定）没有样板，照第一批同风格；税票口径写死：
ERP 不开税票（用户 09-23），任何模板都不印「ใบกำกับภาษี」。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ROWS_PER_PAGE = 10  # 样板每页 10 行
_CENT = Decimal("0.01")

# 领料单「วัตถุประสงค์」四个勾选：ERP 出库用途只有 内部领用 / 报损 / 其他；生产、销售发货 ERP 没有，不打勾
ISSUE_PURPOSES = ("internal", "production", "sales", "other")
ISSUE_PURPOSE_OF = {"internal_use": "internal", "damage": "other", "other": "other"}


def d(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def money(value) -> str:
    """金额 `1,234.00`；没有值（成本未知）留空。"""
    amount = d(value)
    return "" if amount is None else f"{amount.quantize(_CENT, rounding=ROUND_HALF_UP):,.2f}"


def qty(value) -> str:
    """数量去掉多余的 0（`3`、`2.5`），千分位。"""
    number = d(value)
    if number is None:
        return ""
    text = f"{number.normalize():,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def day(value) -> str:
    """日期照样板用公历 日/月/年（`25/10/2026`，主会话 10-26 定，不换佛历）。"""
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        value = date.fromisoformat(str(value)[:10])
    return value.strftime("%d/%m/%Y")


def pages(rows: list, size: int = ROWS_PER_PAGE) -> list:
    """明细按每页 ``size`` 行切；每页补空行到 ``size``（样板的空格子）。没有明细也给一页空表。行带序号 ``no``。"""
    numbered = [{**row, "no": index} for index, row in enumerate(rows, start=1)]
    chunks = [numbered[start:start + size] for start in range(0, len(numbered), size)] or [[]]
    return [chunk + [None] * (size - len(chunk)) for chunk in chunks]


def issue_purpose(purpose: str, reason: str) -> dict:
    """领料单的用途勾选 → ``{checked: {internal, production, sales, other}, other_text}``：报损、其他打在「อื่นๆ」，线上写原因。"""
    picked = ISSUE_PURPOSE_OF.get(str(purpose or ""))
    return {"checked": {key: key == picked for key in ISSUE_PURPOSES},
            "other_text": str(reason or "").strip() if picked == "other" else ""}


def warehouse_check(is_main: bool, name: str) -> dict:
    """收货单「คลังสินค้า」五个框：公司主仓打「คลังหลัก」，其余打「อื่นๆ」并写仓库名；不按仓名硬套「仓 1 / 2 / 3」。"""
    return {"main": bool(is_main), "other": not is_main and bool(name), "other_text": "" if is_main else str(name or "")}


COUNT_ROWS_PER_PAGE = 22  # 盘点表 A4 竖版每页行数

# 单据标题、栏位、付款方式的泰英文字都在模板 `templates/_labels.html`，这里只给键。
# 销售单：接了 Express 的公司正式税票在 Express 出，ERP 打送货单；没接的打非税收据（主会话 10-26 定，写死）。
QUOTATION, RECEIPT_KIND = "quotation", "receipt"
TITLE_KEYS = ("order", "quotation", "delivery", "receipt", "pos_sale", "pos_refund", "shift", "count_sheet",
              "count_result")
BRANCH_HEAD = ("head_office", "hq", "head")


def sales_title(doc_kind: str, linked: bool) -> str:
    """报价单打报价单；销售单接了 Express 打送货单（`delivery`），没接打收据（`receipt`）。"""
    if doc_kind == QUOTATION:
        return "quotation"
    return "delivery" if linked else "receipt"


def pos_title(sale_type: str) -> str:
    """收银小票打收据，不打简易税票；退货打退货单（`pos_refund`），不叫贷项单。"""
    return "pos_refund" if sale_type == "refund" else "pos_sale"


def branch_text(branch_type: str, branch_no: str = "") -> dict:
    """总部 / 分店：``{"head": True}`` / ``{"no": "00001"}``；没登记回空（模板据此印「总部」或「分店 号」）。"""
    kind, number = str(branch_type or ""), str(branch_no or "").strip()
    if kind in BRANCH_HEAD:
        return {"head": True}
    if kind == "branch":
        return {"no": number}
    return {}


def company_branch(branch) -> dict:
    """公司抬头的总部 / 分店：公司档上的分店号是数字串，0 = 总部，其余补成 5 位。"""
    text = str(branch or "").strip()
    if not text:
        return {}
    if text.isdigit():
        return {"head": True} if int(text) == 0 else {"no": f"{int(text):05d}"}
    return {"no": text}


def vehicle_lines(units: list) -> list:
    """按台商品（车辆）行下印的号码：一台一行（车架号 / 发动机号 / 颜色），全空的不印。"""
    return [{key: str(unit.get(key) or "").strip() for key in ("serial_no", "secondary_no", "color")}
            for unit in units or [] if any(str(unit.get(key) or "").strip() for key in ("serial_no", "secondary_no", "color"))]


def count_mode(status: str) -> str:
    """盘点：进行中打点数表（盲盘，不印账面数）；结束 / 过账后打结果表（账面 / 实点 / 差异）。"""
    return "sheet" if str(status or "") == "Active" else "result"


def thermal_height_mm(item_lines: int, extra_lines: int = 0) -> int:
    """热敏 80 mm 存 PDF 时的纸长：PDF 生成器要固定页高，按行数估、宁长勿短（尾部留白不影响），一张不分页。"""
    return int(120 + 9 * max(int(item_lines), 0) + 5 * max(int(extra_lines), 0))


__all__ = ["BRANCH_HEAD", "COUNT_ROWS_PER_PAGE", "ISSUE_PURPOSES", "ROWS_PER_PAGE", "TITLE_KEYS", "branch_text",
           "company_branch", "count_mode", "day", "issue_purpose", "money", "pages", "pos_title", "qty", "sales_title",
           "thermal_height_mm", "vehicle_lines", "warehouse_check"]
