"""复核卡的票面与标记（多产品共用的纯契约；第 2 批识别后复核卡，设计 0.2 / 0.3，主会话 11-10 审定）。

复核卡 = 写账前确认卡（`write_precheck`）+ 一批票的票面与标记。这里只定**形状与规则**，不读不存（产品经钩子读票面、存改动）：

- 票面行（:func:`face`）：``{doc_no, party_name, party_tax, party_branch, date, subtotal, vat, total, lines: [{description, qty,
  unit_price, amount}]}``，金额是字符串（原样保留人填的值，算的时候换 ``Decimal``）。
- 标记码（:data:`MARKS`，前端按码出四语，行上写最短的词）：number 单号、duplicate 重复、tax_id 税号、amount 金额、date 日期。
- 校验（:func:`check`）：税号 13 位校验位、分店 5 位；未税 + 税额 = 合计（到 0.01）；税额 ≈ 未税 × 7%（差 1 泰铢内算平）；明细
  金额 = 数量 × 单价、明细合计 = 未税；日期是真日期（佛历年换公历）。校验不过只标格子、保留人填的值，**不拦开始**（Express
  不拦的我们不拦），唯一例外是写不进 Express 的（``blocking``：单号空、日期不是日期、金额不是数字），那一张这次不写。
- 就地改一格（:func:`edit`）：只收 :data:`EDITABLE` 里的格，佛历日期换公历，改完重算标记（改对了标记消失）。
- 确认（:func:`still_confirmed`）：只写确认过、确认后没再改过的票（按票面版本比）。

纯函数：只依赖标准库与 `tax_documents.amounts`（同样是纯函数）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Tuple

from pearnly_core.tax_documents.amounts import digits, valid_tax_id

NUMBER, DUPLICATE, TAX_ID, AMOUNT, DATE = "number", "duplicate", "tax_id", "amount", "date"
MARKS = (NUMBER, DUPLICATE, TAX_ID, AMOUNT, DATE)
FIELDS = ("doc_no", "party_name", "party_tax", "party_branch", "date", "subtotal", "vat", "total")
LINE_FIELDS = ("description", "qty", "unit_price", "amount")
EDITABLE = frozenset(FIELDS)
MONEY_FIELDS = ("subtotal", "vat", "total")
CENT = Decimal("0.01")
VAT_RATE = Decimal("0.07")
VAT_TOLERANCE = Decimal("1")  # 税额与 7% 差 1 泰铢内算平（同 Cowork `history.review._AMOUNT_TOLERANCE`）
BUDDHIST_OFFSET = 543
TEXT_MAX = 200
LINES_MAX = 200


class ReviewError(ValueError):
    """程序错（改了不能改的格、行号越界）：调用方照抛。"""


def _text(value, limit=TEXT_MAX) -> str:
    return str(value if value is not None else "").strip()[:limit]


def amount(value):
    """金额字符串 → ``Decimal``；空回 None；不是数字回 :data:`NOT_A_NUMBER`。"""
    text = _text(value).replace(",", "").replace("฿", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return NOT_A_NUMBER


NOT_A_NUMBER = object()


def day(value):
    """日期字符串（``YYYY-MM-DD``，佛历年也认）→ ``date``；空回 None；不是日期回 :data:`NOT_A_DATE`。"""
    text = _text(value)
    if not text:
        return None
    try:
        year, month, dom = (int(part) for part in text[:10].split("-"))
        if year > 2400:
            year -= BUDDHIST_OFFSET
        return date(year, month, dom)
    except (ValueError, TypeError):
        return NOT_A_DATE


NOT_A_DATE = object()


def face(fields: Mapping[str, Any]) -> Dict[str, Any]:
    """产品给的票面 → 复核卡一行的形状（只留认识的格，文字截长，日期按 :func:`day` 规整成公历写法）。"""
    out = {key: _text(fields.get(key)) for key in FIELDS}
    parsed = day(out["date"])
    if isinstance(parsed, date):
        out["date"] = parsed.isoformat()
    out["lines"] = [{key: _text(line.get(key)) for key in LINE_FIELDS}
                    for line in (fields.get("lines") or ())[:LINES_MAX] if isinstance(line, Mapping)]
    return out


def check(row: Mapping[str, Any]) -> Dict[str, Any]:
    """一行票面的标记：``{"marks": [码…]（按 MARKS 顺序、去重）, "cells": {格: 码}, "blocking": [格…]}``。
    ``blocking`` 是写不进 Express 的格（这一张这次不写），其余只标不拦。"""
    cells: Dict[str, str] = {}
    blocking: List[str] = []
    if not _text(row.get("doc_no")):
        cells["doc_no"] = NUMBER
        blocking.append("doc_no")
    parsed = day(row.get("date"))
    if parsed is None or parsed is NOT_A_DATE:
        cells["date"] = DATE
        blocking.append("date")
    tax = digits(row.get("party_tax"))
    if row.get("party_tax") and (len(tax) != 13 or not valid_tax_id(tax)):
        cells["party_tax"] = TAX_ID
    branch = digits(row.get("party_branch"))
    if row.get("party_branch") and len(branch) != 5:
        cells["party_branch"] = TAX_ID
    values = {key: amount(row.get(key)) for key in MONEY_FIELDS}
    for key, value in values.items():
        if value is NOT_A_NUMBER:
            cells[key] = AMOUNT
            blocking.append(key)
    sub, vat, total = (values[key] if isinstance(values[key], Decimal) else None for key in MONEY_FIELDS)
    if sub is not None and vat is not None and total is not None and abs(sub + vat - total) > CENT:
        cells["total"] = AMOUNT
    if sub is not None and vat is not None and vat > 0 and abs(sub * VAT_RATE - vat) > VAT_TOLERANCE:
        cells["vat"] = AMOUNT
    line_sum = Decimal(0)
    lines_ok = bool(row.get("lines"))
    for index, line in enumerate(row.get("lines") or ()):
        qty, price, line_amount = (amount(line.get(key)) for key in ("qty", "unit_price", "amount"))
        if not all(isinstance(value, Decimal) for value in (qty, price, line_amount)):
            lines_ok = False
            continue
        if abs(qty * price - line_amount) > CENT:
            cells[f"lines.{index}.amount"] = AMOUNT
        line_sum += line_amount
    if lines_ok and sub is not None and abs(line_sum - sub) > CENT:
        cells["subtotal"] = AMOUNT
    marks = [code for code in MARKS if code in cells.values()]
    return {"marks": marks, "cells": cells, "blocking": blocking}


def edit(row: Mapping[str, Any], field: str, value: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """就地改一格：``field`` 是 :data:`EDITABLE` 里的格或 ``lines.<行>.<列>``。回 (改后的票面, 改后的标记)。
    值原样保留（校验不过也存，只标格子）；日期能认就换成公历写法。"""
    out = face(row)
    if field in EDITABLE:
        text = _text(value)
        if field == "date":
            parsed = day(text)
            text = parsed.isoformat() if isinstance(parsed, date) else text
        out[field] = text
    else:
        head, _sep, rest = str(field or "").partition(".")
        index, _sep, column = rest.partition(".")
        if head != "lines" or column not in LINE_FIELDS or not index.isdigit() or int(index) >= len(out["lines"]):
            raise ReviewError(f"not editable: {field!r}")
        out["lines"][int(index)][column] = _text(value)
    return out, check(out)


def still_confirmed(confirmed_version, current_version) -> bool:
    """确认过、确认后没再改过（票面版本号没变）才写；没确认过（None）不写。"""
    return confirmed_version is not None and str(confirmed_version) == str(current_version)


# 给模型的说法（不经 _()）：写不进 Express 的那几格
BLOCKING_NOTE = "blocking cells cannot go into Express (no number, not a date, not a number): that document is not written this time"

__all__ = ["AMOUNT", "BLOCKING_NOTE", "DATE", "DUPLICATE", "EDITABLE", "FIELDS", "LINE_FIELDS", "MARKS", "NUMBER", "ReviewError",
           "TAX_ID", "amount", "check", "day", "edit", "face", "still_confirmed"]
