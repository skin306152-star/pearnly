# -*- coding: utf-8 -*-
"""客户档字段级 diff（纯函数）。

DMS 里已有的客户 vs 身份证识别出的新值：**只报白名单里真的变了的字段**，
让操作员逐条确认要不要覆盖。

两条铁律：

1. **incoming 缺键或值为空 = 没有这条信息**，绝不拿空值去清 DMS 现值；
2. **`people_id` 是主键身份，永不进 diff**。

归一化吸收无意义差异（首尾/连续空格、泰文数字、电话的连字符）。

照搬老产品 `services/erp/dms_customer_diff.py`。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 允许 diff 的字段：身份 + 户籍地址（地址比 id，不比显示标签）。people_id 不在内（主键）。
_DIFF_FIELDS = (
    "prefix_id",
    "name",
    "birthday_be",
    "phone",
    "house_no",
    "moo",
    "soi",
    "road",
    "province_id",
    "district_id",
    "subdistrict_id",
    "zipcode_id",
)

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _norm(v: Any) -> str:
    """归一：泰文数字→ASCII、连续空白折成一个、首尾去空。"""
    s = str(v if v is not None else "").translate(_THAI_DIGITS)
    return " ".join(s.split())


def _norm_phone(v: Any) -> str:
    """电话按纯数字比：DMS 手工录入常带连字符/空格，"081-234-5678" ≠ "0812345678"
    是**假差异**，会让每次扫卡都弹一次"要不要更新"。全非数字（如 "-" 占位）退回普通归一。
    """
    s = _norm(v)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or s


def diff_customer_fields(current: Dict[str, Any], incoming: Dict[str, Any]) -> List[Dict[str, str]]:
    """比对白名单字段，返回变化项 `[{"field", "old", "new"}]`（归一后的对照值）。

    `incoming` 缺这个键、或值归一后为空 = 无信息，不产生 diff（**绝不覆盖现值**）。
    """
    out: List[Dict[str, str]] = []
    for field in _DIFF_FIELDS:
        if field not in incoming:
            continue
        norm = _norm_phone if field == "phone" else _norm
        new = norm(incoming.get(field))
        if not new:  # 空值 = 无信息
            continue
        old = norm(current.get(field))
        if new != old:
            out.append({"field": field, "old": old, "new": new})
    return out
