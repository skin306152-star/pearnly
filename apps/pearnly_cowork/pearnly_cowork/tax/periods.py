"""税期、日期、佛历、申报期限与增值税税率表。纯函数：只用标准库。

依据（税务局原件，核查记录见任务记录 L4/L5 节）：
- 申报期限：ภ.พ.30 次月 15 日；ภ.ง.ด.3 / 53 付款月结束后 7 天内。电子申报延到次月 23 日 / 月末后 15 天，
  只对法定截止日在 2567-02-01 至 2570-01-31（2024-02-01 至 2027-01-31）之间的申报有效（财政部公告第 7 号）；
  之后要等新公告，这里不猜。
- 增值税：พ.ร.ฎ. ฉบับที่ 807 (2569) 把 7%（含地方附加）延到 2570-09-30（2027-09-30）；表外日期回 None，不写死 7%。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from decimal import Decimal

BE_OFFSET = 543

EFILING_FROM, EFILING_TO = date(2024, 2, 1), date(2027, 1, 31)
PAPER_DAY = {"pp30": 15, "pnd": 7}
EFILING_DAY = {"pp30": 23, "pnd": 15}

# (起, 止, 税率)：纳税义务发生日落在区间内才有税率
VAT_RATES = ((date(2017, 10, 1), date(2027, 9, 30), Decimal("0.07")),)

_YEAR_FIRST = re.compile(r"(\d{4})\s*[-/.]\s*(\d{1,2})")
_MONTH_FIRST = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{4})")
_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DMY = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


def _year(value: int) -> int:
    return value - BE_OFFSET if value >= 2400 else value


def parse_period(text) -> tuple[int, int]:
    """「2026-09」「2026/9」「09/2569」「2569-09」→ (2026, 9)；认不出抛 ValueError("tax.period_invalid")。"""
    raw = str(text or "").strip()
    match = _YEAR_FIRST.fullmatch(raw)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
    else:
        match = _MONTH_FIRST.fullmatch(raw)
        if not match:
            raise ValueError("tax.period_invalid")
        month, year = int(match.group(1)), int(match.group(2))
    year = _year(year)
    if not (1 <= month <= 12 and 1990 <= year <= 2200):
        raise ValueError("tax.period_invalid")
    return year, month


def text(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def index(year: int, month: int) -> int:
    return year * 12 + month - 1


def shift(year: int, month: int, months: int) -> tuple[int, int]:
    value = index(year, month) + months
    return value // 12, value % 12 + 1


def between(earlier: tuple[int, int], later: tuple[int, int]) -> int:
    """later 比 earlier 晚几个月（早于则为负）。"""
    return index(*later) - index(*earlier)


def bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def parse_date(value):
    """date / datetime / 「YYYY-MM-DD」/「DD/MM/YYYY」（佛历年自动换算）→ date；认不出回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()[:10]
    for pattern, order in ((_ISO, "ymd"), (_DMY, "dmy")):
        match = pattern.fullmatch(raw)
        if not match:
            continue
        first, second, third = (int(part) for part in match.groups())
        year, month, day = (first, second, third) if order == "ymd" else (third, second, first)
        try:
            return date(_year(year), month, day)
        except ValueError:
            return None
    return None


def month_of(value):
    found = parse_date(value)
    return (found.year, found.month) if found else None


def be(value) -> str:
    """日/月/佛历年；空回空串。"""
    found = parse_date(value)
    return f"{found.day:02d}/{found.month:02d}/{found.year + BE_OFFSET}" if found else ""


def be_month(year: int, month: int) -> str:
    return f"{month:02d}/{year + BE_OFFSET}"


def deadlines(form: str, year: int, month: int) -> dict:
    """{"paper": date, "efiling": date | None}。form：pp30 / pnd。节假日顺延未核实，不顺延。"""
    next_year, next_month = shift(year, month, 1)
    paper = date(next_year, next_month, PAPER_DAY[form])
    efiling = date(next_year, next_month, EFILING_DAY[form])
    return {"paper": paper, "efiling": efiling if EFILING_FROM <= paper <= EFILING_TO else None}


def vat_rate(value):
    """纳税义务发生日的增值税率；不在表内回 None（调用方不做税率核对）。"""
    found = parse_date(value)
    if not found:
        return None
    for start, end, rate in VAT_RATES:
        if start <= found <= end:
            return rate
    return None
