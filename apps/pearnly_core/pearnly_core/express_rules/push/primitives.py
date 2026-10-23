"""Shared numeric/date parsing, separated to keep payment dependencies acyclic."""
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

def _d(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_invoice_date(raw: Any) -> Optional[date]:
    """票面日期字符串 → 公历 date。认 ISO(YYYY-MM-DD)和 OCR 常见 DD/MM/YYYY 两种记法;
    两者都解不出 / 非法历日(如 2 月 30)→ None。be_dates(佛历格式化)与
    bank_evidence(银行流水 ±7 天窗对账)共用此核 —— 单一日期解析事实源。
    """
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", s)  # DD/MM/YYYY
        if not m2:
            return None
        day, month, year = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None
