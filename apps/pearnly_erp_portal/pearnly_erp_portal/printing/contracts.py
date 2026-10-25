"""单据打印的纯规则（不依赖框架、不做 IO）：版面照岳父样板 1:1，每页 10 行，ERP 没有的栏位留空线手写，不冒充。"""
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


__all__ = ["ISSUE_PURPOSES", "ROWS_PER_PAGE", "day", "issue_purpose", "money", "pages", "qty", "warehouse_check"]
