"""产品列表的原生筛选条件（多产品共用：ERP、Cowork 列表页都走这一份）。

页面上那颗官方 Filter 按钮给的是 Frappe 原生条件 `[字段, 运算符, 值]`（`like` 已由框架补好 `%`，`in` 已拆成数组）。
页面原样交上来（`p.filter_conditions`），服务端在这里按**本页白名单**换成 `frappe.get_list` 的过滤条件，
运算符由 Frappe 自己执行（参数化）。不在白名单的字段、不认识的运算符一律丢掉，不报错也不变成别的条件。

`matches()` 给"不在库里、恒为某个值"的那一侧用（例如销售记录里的收银小票：恒为收据、已收款、来源收银），
口径与 Frappe 对同一条件的结果一致。
"""
from __future__ import annotations

import json
from datetime import date, datetime

OPERATORS = ("=", "!=", "like", "not like", "in", "not in", "is", ">", "<", ">=", "<=", "between", "timespan")
MAX_CONDITIONS = 20


def conditions(raw, allowed: dict) -> list[list]:
    """`raw`：页面交上来的条件（JSON 文本或列表）；`allowed`：`{页面字段: 库里的列}`。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        field, operator, value = row[-3], row[-2], row[-1]
        column = allowed.get(str(field or ""))
        operator = str(operator or "").strip().lower()
        if not column or operator not in OPERATORS:
            continue
        if operator in ("in", "not in", "between"):
            value = [str(item) for item in value] if isinstance(value, (list, tuple)) else [
                item.strip() for item in str(value or "").split(",") if item.strip()]
            if not value:
                continue
        elif isinstance(value, (list, dict)):
            continue
        out.append([column, operator, value])
        if len(out) >= MAX_CONDITIONS:
            break
    return out


def _text(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return "" if value is None else str(value)


def _like(value: str, pattern: str) -> bool:
    import re

    expression = "".join(".*" if char == "%" else "." if char == "_" else re.escape(char) for char in pattern)
    return re.fullmatch(expression, value, flags=re.IGNORECASE | re.DOTALL) is not None


def matches(value, operator: str, wanted) -> bool:
    """恒定值 `value` 是否满足一条条件（用于不在库里的那一侧）。比较不了的运算符按满足处理。"""
    text = _text(value)
    if operator == "=":
        return text == _text(wanted)
    if operator == "!=":
        return text != _text(wanted)
    if operator in ("in", "not in"):
        inside = text in {_text(item) for item in wanted}
        return inside if operator == "in" else not inside
    if operator in ("like", "not like"):
        hit = _like(text, _text(wanted))
        return hit if operator == "like" else not hit
    if operator == "is":
        return bool(text) if _text(wanted) == "set" else not text
    return True
